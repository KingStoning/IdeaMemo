package com.ldlywt.note.backup.cloud

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query

/** Kept in the same database transaction as the note, including remote tombstones. */
@Entity
data class CloudSyncEntry(
    @PrimaryKey val id: String,
    val localId: Long?,
    val revision: Long = 0,
    val snapshot: String = "null",
)

@Dao
interface CloudSyncDao {
    @Query("SELECT * FROM CloudSyncEntry")
    fun all(): List<CloudSyncEntry>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    fun put(entry: CloudSyncEntry)
}
