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
                if("activation".equals(id)&&response.optBoolean("ok")&&response.getJSONObject("data").optBoolean("prepared")){
                    JSONObject payload=new JSONObject(transport.activationBootstrap());byte[] snapshot=android.util.Base64.decode(payload.getString("snapshot_base64"),2);
                    AtomicFile saved=new AtomicFile(new File(getFilesDir(),"activation-fixture-snapshot.json"));FileOutputStream stream=saved.startWrite();
                    try{stream.write(snapshot);saved.finishWrite(stream);}catch(Exception e){saved.failWrite(stream);throw e;}
                    output.put("snapshot_sha256",payload.getString("bootstrap_sha256")).put("snapshot_bytes",snapshot.length);
                }
                AtomicFile file=new AtomicFile(new File(getExternalFilesDir(null),"canonical-probe-result.json"));
                FileOutputStream out=file.startWrite();try{out.write(output.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);}catch(Exception e){file.failWrite(out);throw e;}
                text.setText(response.optBoolean("ok")?"独立连接验收完成":"独立连接拒绝测试完成");
            }catch(Exception e){text.setText("验收回执保存失败");}
        });
        String phase=getIntent().getStringExtra("phase");
        if("prepare".equals(phase))transport.prepareEnrollment();
        else if(phase!=null&&phase.startsWith("capture")){
            try{
                String raw="{\"students\":[{\"id\":1,\"studentNo\":\"TEST001\",\"name\":\"Fictional Student\",\"className\":\"Fictional Class\",\"hours\":10}],\"classes\":[{\"id\":2,\"name\":\"Fictional Class\",\"teacher\":\"Fictional Teacher\",\"courseName\":\"Fictional Course\"}],\"attendance\":[],\"transactions\":[],\"settings\":{\"studentLeaves\":[]}}";
                StringBuilder hash=new StringBuilder();for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8)))hash.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
                if("capture-large".equals(phase)){
                    JSONObject large=new JSONObject(raw);large.getJSONObject("settings").put("fictionalPadding",new String(new char[2*1024*1024]).replace('\0','x'));raw=large.toString();
                    hash.setLength(0);for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8)))hash.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
                }
                transport.capture(raw,"capture-bad-digest".equals(phase)?new String(new char[64]).replace('\0','0'):hash.toString(),System.currentTimeMillis()/1000-("capture-stale".equals(phase)?120:0));
            }catch(Exception e){text.setText("虚构资料准备失败");}
        }
        else if("stage".equals(phase))transport.stage(getIntent().getStringExtra("baseline"));
        else if("activation-prepare".equals(phase))transport.prepareActivationNow(getIntent().getStringExtra("baseline"));
        else if("activation-commit".equals(phase)){
            try{
                byte[] bytes;try(InputStream in=new FileInputStream(new File(getFilesDir(),"activation-fixture-snapshot.json"));ByteArrayOutputStream out=new ByteArrayOutputStream()){
                    byte[] buffer=new byte[8192];int n;while((n=in.read(buffer))!=-1)out.write(buffer,0,n);bytes=out.toByteArray();
                }
                StringBuilder hash=new StringBuilder();for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(bytes))hash.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
                transport.commitActivation(hash.toString());
            }catch(Exception e){text.setText("虚构激活快照缺失");}
        }
        else if("request-next".equals(phase)){
            try{JSONObject p=new JSONObject(transport.overview());transport.request("canonical:"+new String(new char[32]).replace('\0','a'),p.getString("profileId"),"next",new JSONObject().put("generation",p.getString("generation")).toString());}
            catch(Exception e){text.setText("虚构连接未就绪");}
        }
        else finish();
    }
    @Override public void onDestroy(){if(transport!=null)transport.close();super.onDestroy();}
}
