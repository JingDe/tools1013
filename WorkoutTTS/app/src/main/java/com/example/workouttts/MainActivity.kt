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
import com.example.workouttts.databinding.ActivityMainBinding

/**
 * 主界面：选区域 → 申请录屏权限 → 启动前台服务；并接收服务回传的识别结果用于显示。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var projectionManager: MediaProjectionManager
    private var region: Rect? = null

    // 接收 CaptureService 广播回来的识别文字
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val text = intent?.getStringExtra("text") ?: return
            binding.tvLast.text = text
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

    // 选区域结果
    private val regionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val d = result.data!!
            region = Rect(
                d.getIntExtra("left", 0), d.getIntExtra("top", 0),
                d.getIntExtra("right", 0), d.getIntExtra("bottom", 0)
            )
            binding.tvStatus.text = "区域已选: $region"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        projectionManager = getSystemService(MediaProjectionManager::class.java)

        // Android 13+ 需运行时申请通知权限
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        }

        binding.btnSelect.setOnClickListener {
            regionLauncher.launch(Intent(this, RegionActivity::class.java))
        }

        binding.btnStart.setOnClickListener {
            if (region == null) {
                Toast.makeText(this, "请先选择区域", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            captureLauncher.launch(projectionManager.createScreenCaptureIntent())
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

    override fun onStart() {
        super.onStart()
        registerReceiver(receiver, IntentFilter(CaptureService.ACTION_RESULT))
    }

    override fun onStop() {
        super.onStop()
        unregisterReceiver(receiver)
    }
}
