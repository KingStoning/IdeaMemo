package com.ldlywt.note.ui.page.input

import androidx.compose.runtime.mutableStateListOf
import androidx.lifecycle.ViewModel
import com.ldlywt.note.bean.Attachment
import com.ldlywt.note.db.repo.TagNoteRepo
import com.ldlywt.note.backup.cloud.CloudSyncManager
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

@HiltViewModel
class MemoInputViewModel @Inject constructor(
    private val tagNoteRepo: TagNoteRepo,
    val cloudSyncManager: CloudSyncManager,
) : ViewModel() {

    fun deleteResource(path: String) {
        uploadAttachments.remove(uploadAttachments.firstOrNull { it.path == path })
    }

    var uploadAttachments = mutableStateListOf<Attachment>()

}
