package com.ldlywt.note.backup.cloud

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.moriafly.salt.ui.Item
import com.moriafly.salt.ui.RoundedColumn
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.launch

@HiltViewModel
class CloudSyncViewModel @Inject constructor(val manager: CloudSyncManager) : ViewModel() {
    fun sync() { viewModelScope.launch { manager.sync() } }
}

@OptIn(com.moriafly.salt.ui.UnstableSaltApi::class)
@Composable
fun CloudSyncSettings(viewModel: CloudSyncViewModel = hiltViewModel()) {
    val manager = viewModel.manager
    val status by manager.status.collectAsState()
    val busy by manager.busy.collectAsState()
    var open by remember { mutableStateOf(false) }
    RoundedColumn {
        Item(text = "自部署云同步 · 设置", onClick = { if (!busy) open = true })
        Item(text = if (busy) "正在同步…" else "立即双向同步", onClick = { if (!busy) viewModel.sync() })
        Text(status, modifier = Modifier.padding(16.dp))
    }
    if (open) {
        var url by remember { mutableStateOf(manager.serverUrl) }
        var token by remember { mutableStateOf(manager.token) }
        var auto by remember { mutableStateOf(manager.autoSync) }
        var error by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { open = false },
            title = { Text("自部署云同步") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    Text("同步标题、正文、收藏和回收站状态。图片、附件、评论、提醒暂不跨设备同步。")
                    OutlinedTextField(url, { url = it }, label = { Text("服务地址（公网使用 HTTPS）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(token, { token = it }, label = { Text("访问密钥") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                    Text("打开应用时自动同步一次（主页列表下拉可随时手动同步）")
                    Checkbox(checked = auto, onCheckedChange = { auto = it })
                    if (error.isNotBlank()) Text(error)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    try {
                        manager.configure(url, token, auto)
                        open = false
                        viewModel.sync()
                    } catch (e: Exception) {
                        error = e.message ?: "无法保存设置"
                    }
                }) { Text("保存并同步") }
            },
            dismissButton = { TextButton(onClick = { open = false }) { Text("取消") } },
        )
    }
}
