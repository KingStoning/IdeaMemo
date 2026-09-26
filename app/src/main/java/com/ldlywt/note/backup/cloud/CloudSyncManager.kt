package com.ldlywt.note.backup.cloud

import android.content.Context
import com.ldlywt.note.bean.Note
import com.ldlywt.note.bean.NoteTagCrossRef
import com.ldlywt.note.bean.Tag
import com.ldlywt.note.db.AppDatabase
import com.ldlywt.note.utils.TopicUtils
import dagger.hilt.android.qualifiers.ApplicationContext
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

@Serializable
data class CloudNote(
    val title: String, val content: String, val createTime: Long, val updateTime: Long,
    val isCollected: Boolean, val isDeleted: Boolean,
)

@Serializable
private data class Change(val id: String, val baseRevision: Long, val mutationId: String, val note: CloudNote?)
@Serializable
private data class SyncRequest(val protocol: Int = 1, val serverId: String, val changes: List<Change>)
@Serializable
private data class RemoteNote(val id: String, val revision: Long, val note: CloudNote?)
@Serializable
private data class Conflict(val id: String, val copyId: String?, val reason: String)
@Serializable
private data class SyncReply(val protocol: Int, val serverId: String, val notes: List<RemoteNote>, val conflicts: List<Conflict>)
@Serializable
private data class ServerInfo(val protocol: Int, val serverId: String)

@Singleton
class CloudSyncManager @Inject constructor(
    @ApplicationContext context: Context,
    private val db: AppDatabase,
) {
    private val prefs = context.getSharedPreferences("cloud_sync", Context.MODE_PRIVATE)
    private val mutex = Mutex()
    private val json = Json { encodeDefaults = true; ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder().callTimeout(45, TimeUnit.SECONDS)
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).build()
    private val _status = MutableStateFlow("尚未同步")
    val status = _status.asStateFlow()
    private val _busy = MutableStateFlow(false)
    val busy = _busy.asStateFlow()
    val serverUrl: String get() = prefs.getString("url", "") ?: ""
    val token: String get() = prefs.getString("token", "") ?: ""
    val autoSync: Boolean get() = prefs.getBoolean("auto", false)
    private val editorGeneration = java.util.concurrent.atomic.AtomicLong(0)
    @Volatile var editorOpen: Boolean = false
        set(value) {
            field = value
            editorGeneration.incrementAndGet()
        }

    fun configure(url: String, token: String, auto: Boolean) {
        check(!_busy.value) { "请等待当前同步结束" }
        val normalized = url.trim().trimEnd('/')
        val parsed = normalized.toHttpUrlOrNull()
        require(parsed != null && parsed.username.isEmpty() && parsed.password.isEmpty() && parsed.query == null && parsed.fragment == null) {
            "请输入完整的 http:// 或 https:// 服务地址"
        }
        val key = token.trim()
        require(key.length >= 32 && key.all { it.code in 33..126 }) { "密钥至少需要 32 个 ASCII 字符，不能包含空格" }
        check(prefs.edit().putString("url", normalized).putString("token", key).putBoolean("auto", auto).commit()) { "无法保存同步设置" }
    }

    private fun wire(note: Note?): CloudNote? = note?.let {
        CloudNote(it.noteTitle ?: "", it.content, it.createTime, it.updateTime, it.isCollected, it.isDeleted)
    }

    private fun encoded(note: CloudNote?): String = json.encodeToString(note)

    private fun call(url: String, key: String, body: String? = null): String {
        val builder = Request.Builder().url(url).header("Authorization", "Bearer $key")
        if (body != null) builder.post(body.toRequestBody("application/json; charset=utf-8".toMediaType()))
        return http.newCall(builder.build()).execute().use { response ->
            check(response.isSuccessful) {
                when (response.code) {
                    401 -> "访问密钥不正确"
                    413 -> "单次同步超过 8 MiB 限制"
                    in 300..399 -> "服务发生重定向，请填写最终服务地址"
                    else -> "同步服务返回 ${response.code}，本地记录已保留"
                }
            }
            val source = checkNotNull(response.body).source()
            check(!source.request(64L * 1024 * 1024 + 1)) { "同步数据超过 64 MiB 限制" }
            source.readUtf8()
        }
    }

    suspend fun sync(): String = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (editorOpen) return@withLock "编辑中，退出编辑后继续同步"
            val generation = editorGeneration.get()
            _busy.value = true
            _status.value = "正在同步…"
            try {
                val url = serverUrl
                val key = token
                check(url.isNotBlank() && key.isNotBlank()) { "请先填写同步服务地址和密钥" }
                val info = json.decodeFromString<ServerInfo>(call("$url/v1/info", key))
                check(info.protocol == 1) { "同步服务协议不兼容" }
                UUID.fromString(info.serverId)
                val bound = prefs.getString("serverId", null)
                check(bound == null || bound == info.serverId) { "服务器数据卷已改变，请恢复原数据卷后再同步" }
                check(prefs.edit().putString("serverId", info.serverId).commit()) { "无法保存服务身份" }

                var captured = emptyList<Pair<CloudSyncEntry, CloudNote?>>()
                db.runInTransaction {
                    val notes = db.getNoteDao().queryAllData().filter { it.parentNoteId == null }.associateBy { it.noteId }
                    val known = db.getCloudSyncDao().all().mapNotNull { it.localId }.toSet()
                    notes.values.filter { it.noteId !in known }.forEach {
                        db.getCloudSyncDao().put(CloudSyncEntry(UUID.randomUUID().toString(), it.noteId))
                    }
                    captured = db.getCloudSyncDao().all().map { it to wire(notes[it.localId]) }
                }
                val changes = captured.filter { (entry, note) -> entry.snapshot != encoded(note) }.map { (entry, note) ->
                    val seed = "${entry.id}:${entry.revision}:${encoded(note)}"
                    val mutation = MessageDigest.getInstance("SHA-256").digest(seed.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
                    Change(entry.id, entry.revision, mutation, note)
                }
                val body = json.encodeToString(SyncRequest(serverId = info.serverId, changes = changes))
                check(changes.size <= 5000 && body.toByteArray(Charsets.UTF_8).size <= 8 * 1024 * 1024) { "单次同步限制为 5000 条修改、8 MiB" }
                val reply = json.decodeFromString<SyncReply>(call("$url/v1/sync", key, body))
                check(reply.protocol == 1 && reply.serverId == info.serverId) { "服务响应身份不匹配" }
                if (editorOpen || generation != editorGeneration.get()) {
                    _status.value = "编辑中，远端更改将在退出编辑后接收"
                    return@withLock _status.value
                }
                val before = captured.associate { it.first.id to it.second }
                db.runInTransaction {
                    val entries = db.getCloudSyncDao().all().associateBy { it.id }
                    val notes = db.getNoteDao().queryAllData().associateBy { it.noteId }.toMutableMap()
                    reply.notes.forEach { remote ->
                        val entry = entries[remote.id]
                        val local = entry?.localId?.let { notes[it] }
                        // Never overwrite an edit or permanent deletion made while the request was in flight.
                        if (entry != null && encoded(wire(local)) != encoded(before[remote.id])) return@forEach
                        if (entry != null && entry.revision == remote.revision && entry.snapshot == encoded(remote.note) && entry.snapshot == encoded(wire(local))) return@forEach
                        var localId = entry?.localId
                        if (remote.note == null) {
                            if (local != null) db.getNoteDao().delete(local)
                            localId = null
                        } else {
                            val n = remote.note
                            val merged = (local ?: Note()).copy(
                                noteTitle = n.title, content = n.content, createTime = n.createTime,
                                updateTime = n.updateTime, isCollected = n.isCollected, isDeleted = n.isDeleted,
                            )
                            if (local == null) {
                                localId = db.getNoteDao().insert(merged)
                            } else {
                                db.getNoteDao().update(merged) // UPDATE keeps Android attachments, comments and reminders.
                            }
                            updateTags(checkNotNull(localId), n.content)
                        }
                        db.getCloudSyncDao().put(CloudSyncEntry(remote.id, localId, remote.revision, encoded(remote.note)))
                    }
                    db.getTagDao().refreshCounts()
                }
                val time = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault()).format(java.util.Date())
                _status.value = if (reply.conflicts.isEmpty()) "同步完成 · $time" else "同步完成 · ${reply.conflicts.size} 处冲突已保留，请检查冲突副本或恢复的记录"
                _status.value
            } catch (e: kotlinx.coroutines.CancellationException) {
                _status.value = "同步已中断，本地记录已保留"
                throw e
            } catch (e: Exception) {
                _status.value = "同步失败，本地记录已保留：${e.message ?: "网络异常"}"
                _status.value
            } finally {
                _busy.value = false
            }
        }
    }

    private fun updateTags(noteId: Long, content: String) {
        val desired = TopicUtils.getTopicListByString(content).ifEmpty { listOf(Tag(tag = "")) }
        val refs = db.getNoteTagCrossRefDao()
        // Insert the replacement references first: upstream triggers delete a note when its last tag disappears.
        desired.forEach {
            db.getTagDao().insertIfAbsent(it)
            refs.insertNoteTagCrossRef(NoteTagCrossRef(noteId = noteId, tag = it.tag))
        }
        val names = desired.map { it.tag }.toSet()
        refs.forNote(noteId).filter { it.tag !in names }.forEach(refs::deleteCrossRef)
    }
}
