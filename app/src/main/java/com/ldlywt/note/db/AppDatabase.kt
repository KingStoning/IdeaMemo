package com.ldlywt.note.db

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.ldlywt.note.bean.*
import com.ldlywt.note.backup.cloud.CloudSyncEntry
import com.ldlywt.note.backup.cloud.CloudSyncDao
import com.ldlywt.note.db.dao.NoteDao
import com.ldlywt.note.db.dao.NoteTagCrossRefDao
import com.ldlywt.note.db.dao.TagDao
import com.ldlywt.note.db.dao.TagNoteDao

@Database(
    entities = [
        Note::class,
        Tag::class,
        NoteTagCrossRef::class,
        Comment::class,
        Reminder::class,
        CloudSyncEntry::class,
    ], version = 3, exportSchema = false
)
@TypeConverters(DatabaseConverters::class)
abstract class AppDatabase : RoomDatabase() {
    //创建DAO的抽象类
    abstract fun getNoteDao(): NoteDao
    abstract fun getTagDao(): TagDao
    abstract fun getTagNote(): TagNoteDao
    abstract fun getNoteTagCrossRefDao(): NoteTagCrossRefDao
    abstract fun getCloudSyncDao(): CloudSyncDao

    companion object {
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE TABLE IF NOT EXISTS CloudSyncEntry (id TEXT NOT NULL PRIMARY KEY, localId INTEGER, revision INTEGER NOT NULL, snapshot TEXT NOT NULL)")
            }
        }
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE Note ADD COLUMN parent_note_id INTEGER DEFAULT NULL")
            }
        }
    }
}
