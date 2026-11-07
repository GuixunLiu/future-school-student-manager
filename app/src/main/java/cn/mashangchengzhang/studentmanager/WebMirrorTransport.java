package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import android.util.Base64;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.X509Certificate;
import java.security.spec.MGF1ParameterSpec;
import java.util.*;
import java.util.concurrent.*;
import javax.crypto.*;
import javax.crypto.spec.*;
import javax.net.ssl.*;

/** Durable read-only snapshots. Credentials never cross the WebView bridge. */
final class WebMirrorTransport {
    private static final String RSA="school_web_mirror_rsa_v1", AES="school_web_mirror_aes_v1";
    private final Activity activity;
    private final SharedPreferences prefs;
    private final AtomicFile pending;
    private final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor();
    private final Runnable changed;
    private final String trustedPin;
    private volatile boolean closed, importing;
    private long retrySeconds=5;
    private ScheduledFuture<?> retry;

    WebMirrorTransport(Activity a,Runnable changed) {
        activity=a;this.changed=changed;prefs=a.getSharedPreferences("web_mirror_native_v1",0);
        pending=new AtomicFile(new File(a.getFilesDir(),"web-mirror-pending.json"));
        String pin="";try(InputStream in=a.getResources().openRawResource(R.raw.web_mirror_server_spki)){pin=new String(read(in,256),StandardCharsets.US_ASCII).trim();}catch(Exception ignored){}
        trustedPin=pin;
        worker.execute(()->{try{exportRequest();}catch(Exception ignored){status("连接申请准备失败");}flush();});
    }
    private KeyStore store() throws Exception {KeyStore k=KeyStore.getInstance("AndroidKeyStore");k.load(null);return k;}
    private PrivateKey rsa() throws Exception {
        KeyStore k=store();if(!k.containsAlias(RSA)){
            KeyPairGenerator g=KeyPairGenerator.getInstance("RSA","AndroidKeyStore");
            g.initialize(new KeyGenParameterSpec.Builder(RSA,KeyProperties.PURPOSE_DECRYPT).setKeySize(3072)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP).setDigests(KeyProperties.DIGEST_SHA256,KeyProperties.DIGEST_SHA1).build());g.generateKeyPair();
        }return (PrivateKey)store().getKey(RSA,null);
    }
    private SecretKey aes() throws Exception {
        KeyStore k=store();if(!k.containsAlias(AES)){
            KeyGenerator g=KeyGenerator.getInstance("AES","AndroidKeyStore");g.init(new KeyGenParameterSpec.Builder(AES,3).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());g.generateKey();
        }return (SecretKey)store().getKey(AES,null);
    }
    private static String ref(){return UUID.randomUUID().toString().replace("-","");}
    private File external(String name)throws Exception{File root=activity.getExternalFilesDir(null);if(root==null)throw new IOException();return new File(root,name);}
    private void exportRequest()throws Exception{
        rsa();String nonce=prefs.getString("nonce","");if(nonce.isEmpty()){nonce=ref();if(!prefs.edit().putString("nonce",nonce).commit())throw new IOException();}
        JSONObject o=new JSONObject().put("protocol",1).put("nonce",nonce).put("public_key",Base64.encodeToString(store().getCertificate(RSA).getPublicKey().getEncoded(),Base64.NO_WRAP));
        AtomicFile f=new AtomicFile(external("web-mirror-request.json"));write(f,o.toString().getBytes(StandardCharsets.UTF_8));
    }
    private JSONObject profile()throws Exception{
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,aes(),new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),2)));
        return new JSONObject(new String(c.doFinal(Base64.decode(prefs.getString("secret",""),2)),StandardCharsets.UTF_8));
    }
    private void saveProfile(JSONObject p)throws Exception{
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,aes());
        String secret=Base64.encodeToString(c.doFinal(p.toString().getBytes(StandardCharsets.UTF_8)),2);
        if(!prefs.edit().putString("secret",secret).putString("iv",Base64.encodeToString(c.getIV(),2)).putString("device",p.getString("d")).putBoolean("enabled",true).putString("state","已连接，等待提交").commit())throw new IOException();
    }
    synchronized String overview(){
        try{return new JSONObject().put("configured",prefs.contains("secret")).put("enabled",prefs.getBoolean("enabled",false)).put("state",prefs.getString("state","尚未连接网页"))
            .put("revision",prefs.getLong("acked",0)).put("received_at",prefs.getLong("received",0)).put("pending",pending.getBaseFile().exists()).toString();}catch(Exception e){return "{}";}
    }
    private void status(String value){prefs.edit().putString("state",value).commit();notifyChanged();}
    private void notifyChanged(){if(!closed)activity.runOnUiThread(()->{if(!closed)changed.run();});}
    void configure(){
        if(activity.getSharedPreferences(CanonicalSyncTransport.PREFS,0).getBoolean("enabled",false))return;
        activity.runOnUiThread(()->{
            if(closed||importing)return;
            String message=prefs.contains("secret")?"重新验证连接后，将继续以平板资料更新网页。签到和课时只在平板记一次。":"连接后，签到、请假、课时及班级任课变更会自动更新网页。只发送教学所需资料，不发送人脸、照片和联系方式。";
            new AlertDialog.Builder(activity).setTitle("网页自动同步").setMessage(message).setNegativeButton("取消",null).setNeutralButton("暂停同步",(d,w)->pause())
                .setPositiveButton("验证并启用",(d,w)->{importing=true;status("正在验证加密连接，请稍候");worker.execute(()->{
                    String stage="连接文件";
                    try{
                        JSONObject p;
                        File file=external("web-mirror-connection.enc.json");
                        if(file.isFile()){
                            JSONObject encrypted=new JSONObject(new String(read(new FileInputStream(file),4096),StandardCharsets.UTF_8));
                            Cipher c=Cipher.getInstance("RSA/ECB/OAEPPadding");c.init(Cipher.DECRYPT_MODE,rsa(),new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
                            p=new JSONObject(new String(c.doFinal(Base64.decode(encrypted.getString("ciphertext"),2)),StandardCharsets.UTF_8));
                            if(!p.getString("n").equals(prefs.getString("nonce","")))throw new Exception();
                        }else p=profile();
                        validateProfile(p);stage="加密连接";JSONObject plan=http(p,"/v1/tablet-mirror/plan",null);
                        stage="服务器授权";
                        if(plan.getInt("protocol")!=1||!plan.getBoolean("enabled")||!plan.getString("device_id").equals(p.getString("d")))throw new Exception();
                        if(!prefs.getString("device",p.getString("d")).equals(p.getString("d"))&&prefs.getLong("acked",0)>0)throw new Exception();
                        saveProfile(p);if(file.isFile())file.delete();retrySeconds=5;flush();notifyChanged();
                    }catch(Exception e){
                        String reason=stage+"未通过";
                        if(e instanceof Rejected)reason="服务器授权未通过（"+((Rejected)e).code+"）";
                        else if(e instanceof java.net.SocketTimeoutException)reason="网络连接超时，请确认平板能上网";
                        else if(e instanceof java.net.ConnectException||e instanceof java.net.NoRouteToHostException)reason="暂时无法连到服务器，请检查平板网络";
                        else if(e instanceof SSLException)reason="服务器加密验证未通过（"+e.getClass().getSimpleName()+"）";
                        else reason+="（"+e.getClass().getSimpleName()+"）";
                        status(reason+"，本机资料保留");
                    }finally{importing=false;}
                });}).show();
        });
    }
    synchronized void pause(){prefs.edit().putBoolean("enabled",false).putString("state","同步已暂停，本机资料保留").commit();notifyChanged();}
    private void validateProfile(JSONObject p)throws Exception{
        String endpoint=p.getString("e");URL u=new URL(endpoint);
        if(!trustedPin.matches("[a-f0-9]{64}")||!trustedPin.equals(p.getString("p"))||!p.getString("t").matches("wxd_[A-Za-z0-9_-]{43}")||!p.getString("d").matches("[a-f0-9]{32}"))throw new Exception();
        if(!endpoint.equals("https://47.97.226.219:18083")&&!(BuildConfig.DEBUG&&endpoint.matches("https://127\\.0\\.0\\.1:[0-9]{4,5}")))throw new Exception();
        if(!u.getProtocol().equals("https"))throw new Exception();
    }
    private static String digest(byte[] bytes)throws Exception{byte[] h=MessageDigest.getInstance("SHA-256").digest(bytes);StringBuilder s=new StringBuilder();for(byte b:h)s.append(String.format(Locale.ROOT,"%02x",b&255));return s.toString();}
    private JSONObject http(JSONObject p,String route,byte[] bytes)throws Exception{
        validateProfile(p);
        X509TrustManager trust=new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] c,String a)throws java.security.cert.CertificateException{throw new java.security.cert.CertificateException();}
            public void checkServerTrusted(X509Certificate[] chain,String a)throws java.security.cert.CertificateException{
                try{if(chain==null||chain.length!=1||!trustedPin.equals(digest(chain[0].getPublicKey().getEncoded())))throw new Exception();chain[0].checkValidity();chain[0].verify(chain[0].getPublicKey());}
                catch(Exception e){throw new java.security.cert.CertificateException("server identity");}
            }};
        SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(null,new TrustManager[]{trust},null);
        HttpsURLConnection c=(HttpsURLConnection)new URL(p.getString("e")+route).openConnection();
        try{
            c.setSSLSocketFactory(ssl.getSocketFactory()); // Default hostname verification is retained.
            c.setInstanceFollowRedirects(false);c.setConnectTimeout(8000);c.setReadTimeout(20000);c.setUseCaches(false);
            c.setRequestProperty("Authorization","Bearer "+p.getString("t"));c.setRequestProperty("Accept","application/json");c.setRequestProperty("Connection","close");c.setRequestProperty("Cookie","");
            if(bytes!=null){c.setRequestMethod("POST");c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);c.setFixedLengthStreamingMode(bytes.length);try(OutputStream out=c.getOutputStream()){out.write(bytes);}}
            int code=c.getResponseCode();if(code!=200)throw new Rejected(code);
            return new JSONObject(new String(read(c.getInputStream(),32768),StandardCharsets.UTF_8));
        }finally{c.disconnect();}
    }
    private static final class Rejected extends IOException{final int code;Rejected(int c){code=c;}}
    private static byte[] read(InputStream stream,int max)throws IOException{
        try(InputStream in=stream;ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>max)throw new IOException("limit");out.write(b,0,n);}return out.toByteArray();}
    }
    private static void write(AtomicFile f,byte[] data)throws IOException{FileOutputStream out=null;try{out=f.startWrite();out.write(data);f.finishWrite(out);}catch(IOException e){if(out!=null)f.failWrite(out);throw e;}}
    private static void fields(JSONObject row,String allowed)throws Exception{
        Set<String> names=new HashSet<>(Arrays.asList(allowed.split(" ")));if(row.length()!=names.size())throw new Exception();
        Iterator<String> it=row.keys();while(it.hasNext())if(!names.contains(it.next()))throw new Exception();
    }
    /** Reject any accidental private fields, including nested data, before network access. */
    static void validateData(JSONObject o)throws Exception{
        fields(o,"format schema_version as_of_date source_captured_at source_records classes students weeks lessons history_import_is_read_only balances_charged wechat_enabled");
        if(!o.getString("format").equals("future-school-teacher-web-dataset")||o.getInt("schema_version")!=1||!o.getBoolean("history_import_is_read_only")||o.getBoolean("balances_charged")||o.getBoolean("wechat_enabled"))throw new Exception();
        for(String name:new String[]{"classes","students","weeks","lessons"}){
            JSONArray rows=o.getJSONArray(name);if(rows.length()>(name.equals("lessons")?30000:name.equals("students")?10000:name.equals("weeks")?262:1000))throw new Exception();
            for(int i=0;i<rows.length();i++){
                JSONObject row=rows.getJSONObject(i);
                switch(name){
                    case "classes":fields(row,"local_id name course_name teacher");break;
                    case "students":fields(row,"local_id name external_ref remaining_units");break;
                    case "weeks":fields(row,"offset start end name expected arrived leave_count rate needs_review fallback classes teachers");
                        for(String g:new String[]{"classes","teachers"}){JSONArray groups=row.getJSONArray(g);for(int j=0;j<groups.length();j++)fields(groups.getJSONObject(j),"name expected arrived leave_count rate needs_review");}break;
                    case "lessons":fields(row,"key class_local_id class_name teacher date kind start_time end_time expected arrived leave_count needs_review members");
                        JSONArray members=row.getJSONArray("members");for(int j=0;j<members.length();j++)fields(members.getJSONObject(j),"student_local_id name external_ref remaining_units expected status attendance_time attendance_method makeup");break;
                }
                Iterator<String> keys=row.keys();while(keys.hasNext()){Object v=row.get(keys.next());if(v instanceof String&&((String)v).length()>200)throw new Exception();}
            }
        }
    }
    void submit(String raw){
        if(activity.getSharedPreferences(CanonicalSyncTransport.PREFS,0).getBoolean("enabled",false))return;
        if(closed||!prefs.getBoolean("enabled",false)||raw==null)return;
        if(raw.length()>4*1024*1024){status("同步资料超出范围，请联系管理员");return;}
        worker.execute(()->{
            if(!prefs.getBoolean("enabled",false))return;
            try{
                JSONObject data=new JSONObject(raw);validateData(data);JSONObject semantic=new JSONObject(raw);semantic.remove("source_captured_at");
                String hash=digest(semantic.toString().getBytes(StandardCharsets.UTF_8));
                if(hash.equals(prefs.getString("queued_hash",""))){flush();return;}
                long revision=prefs.getLong("sequence",0)+1;
                if(!prefs.edit().putLong("sequence",revision).commit())throw new IOException();
                JSONObject envelope=new JSONObject().put("protocol",1).put("device_id",prefs.getString("device","")).put("revision",revision).put("request_ref",ref()).put("data",data);
                byte[] bytes=envelope.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>4*1024*1024)throw new IOException();
                write(pending,bytes);prefs.edit().putString("queued_hash",hash).commit();status("已在本机排队，正在更新网页");flush();
            }catch(Exception e){status("资料待核对，本机保存不受影响");}
        });
    }
    private void flush(){
        if(activity.getSharedPreferences(CanonicalSyncTransport.PREFS,0).getBoolean("enabled",false))return;
        if(closed||!prefs.getBoolean("enabled",false)||!pending.getBaseFile().exists())return;
        try{
            byte[] raw=pending.readFully();JSONObject envelope=new JSONObject(new String(raw,StandardCharsets.UTF_8));
            // A full view from a previous day must be rebuilt from current local facts.
            // Retain it until the WebView has durably replaced it; never pause on an obsolete view.
            String today=new java.text.SimpleDateFormat("yyyy-MM-dd",Locale.ROOT).format(new Date());
            if(!today.equals(envelope.getJSONObject("data").getString("as_of_date"))){
                if(!prefs.getString("state","").equals("正在读取今天的最新资料"))status("正在读取今天的最新资料");
                return;
            }
            JSONObject receipt=http(profile(),"/v1/tablet-mirror/snapshot",raw);
            if(!receipt.getString("device_id").equals(envelope.getString("device_id"))||receipt.getLong("revision")!=envelope.getLong("revision")||!receipt.getString("request_ref").equals(envelope.getString("request_ref"))||!receipt.getString("source_sha256").equals(digest(raw))||receipt.getBoolean("hours_deducted"))throw new IOException();
            if(!prefs.edit().putLong("acked",receipt.getLong("revision")).putLong("received",receipt.getLong("received_at")).putString("state","网页已更新").commit())throw new IOException();
            pending.delete();retrySeconds=5;notifyChanged();
        }catch(Rejected e){
            if(e.code==401||e.code==403||e.code==409||e.code==422){prefs.edit().putBoolean("enabled",false).commit();status("服务器要求核对，自动同步已暂停（"+e.code+"）");}
            else retryLater();
        }catch(Exception e){retryLater();}
    }
    private void retryLater(){status("网络暂不可用，本机已保存，将自动重试");if(!closed){if(retry!=null)retry.cancel(false);retry=worker.schedule(this::flush,retrySeconds,TimeUnit.SECONDS);retrySeconds=Math.min(300,retrySeconds*2);}}
    void resume(){if(!closed)worker.execute(this::flush);}
    void close(){closed=true;worker.shutdownNow();}
}
