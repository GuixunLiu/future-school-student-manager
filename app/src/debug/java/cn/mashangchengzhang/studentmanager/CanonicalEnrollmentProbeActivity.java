package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.os.Bundle;
import android.util.AtomicFile;
import android.widget.TextView;
import java.io.*;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Debug-only native test, no WebView, student data or production component. */
public final class CanonicalEnrollmentProbeActivity extends Activity {
    private CanonicalSyncTransport transport;
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        if(!BuildConfig.DEBUG||!getPackageName().endsWith(".synctest")){finish();return;}
        TextView text=new TextView(this);text.setText("独立连接验收 · 虚构资料");setContentView(text);
        final String ref=getIntent().getStringExtra("run_ref");
        transport=new CanonicalSyncTransport(this,(id,response)->{
            try{
                JSONObject output=new JSONObject().put("run_ref",ref).put("response",response).put("profile",new JSONObject(transport.overview()));
                AtomicFile file=new AtomicFile(new File(getExternalFilesDir(null),"canonical-probe-result.json"));
                FileOutputStream out=file.startWrite();try{out.write(output.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);}catch(Exception e){file.failWrite(out);throw e;}
                text.setText(response.optBoolean("ok")?"独立连接验收完成":"独立连接拒绝测试完成");
            }catch(Exception e){text.setText("验收回执保存失败");}
        });
        String phase=getIntent().getStringExtra("phase");
        if("prepare".equals(phase))transport.prepareEnrollment();
        else if("stage".equals(phase))transport.stage(getIntent().getStringExtra("baseline"));
        else finish();
    }
    @Override public void onDestroy(){if(transport!=null)transport.close();super.onDestroy();}
}
