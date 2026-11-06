package cn.mashangchengzhang.futureschool.shell;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * 精简 WebView 壳：加载 Vue SPA（前后端分离版）。
 *
 * 默认加载开发服务器 http://10.0.2.2:5173（模拟器访问宿主机）。
 * 可用 adb 传参覆盖：
 *   adb shell am start -n <pkg>/.ShellActivity --es url http://10.0.2.2:5173/
 *
 * 说明：
 * - 登录态、设备令牌、离线 outbox 全部在 Web 端 IndexedDB/localStorage，壳不感知
 * - 人脸识别本地 WASM 链路暂未接入（保留在原 App）
 * - 生产打包形态（本地静态资源 + 回环服务）在阶段4后续决定
 */
public class ShellActivity extends Activity {

    private static final String DEFAULT_URL = "http://10.0.2.2:5173/";
    private WebView webView;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        webView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(webView);

        webView.getSettings().setJavaScriptEnabled(true);
        // localStorage / IndexedDB 依赖 DOM Storage
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setDatabaseEnabled(true);
        webView.setWebViewClient(new WebViewClient());

        String url = getIntent().getStringExtra("url");
        webView.loadUrl(url == null ? DEFAULT_URL : url);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
