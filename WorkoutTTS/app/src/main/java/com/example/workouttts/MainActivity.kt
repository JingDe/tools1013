package com.example.workouttts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Rect
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.example.workouttts.databinding.ActivityMainBinding

/**
 * 主界面：选区域（基于截图，免悬浮窗权限）→ 申请录屏权限 → 启动前台服务；
 * 并接收服务回传的识别结果用于显示。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var projectionManager: MediaProjectionManager
    private var region: Rect? = null

    private val regionPrefs get() = getSharedPreferences("region_prefs", MODE_PRIVATE)

    // 接收 CaptureService 广播回来的识别文字
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val text = intent?.getStringExtra("text") ?: return
            binding.tvLast.text = text
        }
    }

    // 接收 CaptureService 广播回来的错误信息（OCR 失败 / 异常），直接显示在状态栏
    private val errorReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val msg = intent?.getStringExtra("msg") ?: return
            binding.tvStatus.text = "错误: $msg"
        }
    }

    // 接收 CaptureService 广播回来的普通提示（如调试帧保存成功）
    private val infoReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val msg = intent?.getStringExtra("msg") ?: return
            binding.tvStatus.text = "提示: $msg"
        }
    }

    // 申请屏幕采集权限（系统会弹一次授权框）
    private val captureLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val intent = Intent(this, CaptureService::class.java).apply {
                putExtra("resultCode", result.resultCode)
                putExtra("data", result.data)
                region?.let { putExtra("region", it) }
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            binding.tvStatus.text = "监控中…（动作变化时会朗读）"
        } else {
            Toast.makeText(this, "未授权屏幕采集，无法开始", Toast.LENGTH_SHORT).show()
        }
    }

    // 选区域结果（来自 PickRegionActivity，基于截图拖框）
    private val regionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val d = result.data!!
            region = Rect(
                d.getIntExtra("left", 0), d.getIntExtra("top", 0),
                d.getIntExtra("right", 0), d.getIntExtra("bottom", 0)
            )
            saveRegionPrefs(region!!)
            binding.tvStatus.text = "区域已选: $region"
        } else {
            Toast.makeText(this, "未选择区域", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 显示上一次崩溃信息（崩溃捕获已移到 App 层），便于无电脑联调直接读原因
        val lastCrash = getSharedPreferences("crash_prefs", MODE_PRIVATE)
            .getString("last_crash", null)
        if (!lastCrash.isNullOrBlank()) {
            binding.tvStatus.text = "上次崩溃: ${lastCrash.split("\n").first()}"
        }

        projectionManager = getSystemService(MediaProjectionManager::class.java)

        // Android 13+ 需运行时申请通知权限
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        }

        loadRegionFromPrefs()

        binding.btnSelect.setOnClickListener {
            regionLauncher.launch(Intent(this, PickRegionActivity::class.java))
        }

        binding.btnStart.setOnClickListener {
            // 不强制要求选区域：region 为空时服务走“智能识别（整屏自动）”模式
            captureLauncher.launch(projectionManager.createScreenCaptureIntent())
        }

        binding.btnSmart.setOnClickListener {
            // 清除已选区域，切换到整屏自动识别（无需画框，不受屏幕方向/全屏限制）
            region = null
            regionPrefs.edit().clear().apply()
            binding.tvStatus.text = "已切换：智能识别（整屏自动），可直接点②开始监控"
            Toast.makeText(this, "智能识别模式：整屏自动找动作名，不用框", Toast.LENGTH_SHORT).show()
        }

        binding.btnStop.setOnClickListener {
            stopService(Intent(this, CaptureService::class.java))
            binding.tvStatus.text = "已停止"
        }

        binding.btnTest.setOnClickListener {
            if (!CaptureService.isRunning) {
                Toast.makeText(this, "请先开始监控再测试", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val i = Intent(this, CaptureService::class.java).apply {
                action = CaptureService.ACTION_TEST
                region?.let { putExtra("region", it) }
            }
            startService(i)
        }
    }

    private fun saveRegionPrefs(r: Rect) {
        regionPrefs.edit()
            .putInt("left", r.left).putInt("top", r.top)
            .putInt("right", r.right).putInt("bottom", r.bottom).apply()
    }

    /** 从本地偏好读取已保存的选框，并刷新状态栏。无区域则默认智能识别模式。 */
    private fun loadRegionFromPrefs() {
        val p = regionPrefs
        if (p.contains("left")) {
            region = Rect(p.getInt("left", 0), p.getInt("top", 0), p.getInt("right", 0), p.getInt("bottom", 0))
            binding.tvStatus.text = "区域已选: $region（手动模式）"
        } else {
            binding.tvStatus.text = "智能识别模式（整屏自动）：可直接点②开始监控"
        }
    }

    override fun onStart() {
        super.onStart()
        // Android 14 (API 34) 起，context-registered receiver 必须指定 exported 标志，否则抛 SecurityException
        ContextCompat.registerReceiver(this, receiver, IntentFilter(CaptureService.ACTION_RESULT), ContextCompat.RECEIVER_NOT_EXPORTED)
        ContextCompat.registerReceiver(this, errorReceiver, IntentFilter(CaptureService.ACTION_ERROR), ContextCompat.RECEIVER_NOT_EXPORTED)
        ContextCompat.registerReceiver(this, infoReceiver, IntentFilter(CaptureService.ACTION_INFO), ContextCompat.RECEIVER_NOT_EXPORTED)
    }

    override fun onStop() {
        super.onStop()
        unregisterReceiver(receiver)
        unregisterReceiver(errorReceiver)
        unregisterReceiver(infoReceiver)
    }
}
