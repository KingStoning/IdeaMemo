package com.ldlywt.note.db.dao

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.ldlywt.note.bean.NoteTagCrossRef

@Dao
interface NoteTagCrossRefDao {
    @Query("SELECT * FROM NoteTagCrossRef WHERE note_id = :noteId")
    fun forNote(noteId: Long): List<NoteTagCrossRef>

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insertNoteTagCrossRef(entity: NoteTagCrossRef)

    @Delete
    fun deleteCrossRef(entity: NoteTagCrossRef)

}
