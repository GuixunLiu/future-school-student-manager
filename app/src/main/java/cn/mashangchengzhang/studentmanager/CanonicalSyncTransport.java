package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.content.SharedPreferences;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.io.*;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.*;

/** Fixed-route pinned client. No token enters JavaScript; no auto-enrolment.
 * Explicit migration tooling must provision a Keystore-encrypted native profile.
 * Existing mirror credentials are never silently reused for canonical writes.
 */
final class CanonicalSyncTransport {
    interface Callback { void reply(String id, JSONObject response); }
    static final String PREFS="canonical_sync_native_v1", AES="school_canonical_sync_aes_v1";
    private final Activity activity;
    private final SharedPreferences prefs;
    private final Callback callback;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy=new AtomicBoolean(false);
    private final String pin;
    private volatile boolean closed;
    CanonicalSyncTransport(Activity a,Callback cb){
        activity=a;callback=cb;prefs=a.getSharedPreferences(PREFS,0);
        String value="";try(InputStream in=a.getResources().openRawResource(R.raw.web_mirror_server_spki)){value=new String(read(in,256),StandardCharsets.US_ASCII).trim();}catch(Exception ignored){}
        pin=value;
    }
    private JSONObject profile()throws Exception{
        if(!prefs.getBoolean("enabled",false))throw new IOException("disabled");
        KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);
        SecretKey key=(SecretKey)keys.getKey(AES,null);if(key==null)throw new IOException("unprovisioned");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),2)));
        JSONObject p=new JSONObject(new String(cipher.doFinal(Base64.decode(prefs.getString("secret",""),2)),StandardCharsets.UTF_8));
        validate(p);return p;
    }
    private void validate(JSONObject p)throws Exception{
        if(!pin.matches("[a-f0-9]{64}")||!pin.equals(p.getString("p"))||!p.getString("t").matches("wxd_[A-Za-z0-9_-]{43}"))throw new IOException("profile");
        for(String k:new String[]{"o","d","g","id"})if(!p.getString(k).matches("[a-f0-9]{32}"))throw new IOException("profile");
        String endpoint=p.getString("e");
        if(!endpoint.equals("https://47.97.226.219:18083")&&!(BuildConfig.DEBUG&&endpoint.matches("https://127\\.0\\.0\\.1:[0-9]{4,5}")))throw new IOException("endpoint");
    }
    String overview(){
        try{JSONObject p=profile();return new JSONObject().put("configured",true).put("orgId",p.getString("o")).put("deviceId",p.getString("d"))
            .put("generation",p.getString("g")).put("profileId",p.getString("id")).toString();}catch(Exception e){return "{\"configured\":false}";}
    }
    void request(String id,String profileId,String kind,String text){
        if(closed||id==null||!id.matches("canonical:[a-f0-9]{32}")||text==null||text.length()>16000)return;
        if(!busy.compareAndSet(false,true)){reply(id,result(false,429,null));return;}
        worker.execute(()->{
            try{
                JSONObject p=profile();if(!p.getString("id").equals(profileId))throw new IOException("profile changed");
                JSONObject body=new JSONObject(text);
                if(!body.getString("generation").equals(p.getString("g")))throw new IOException("generation");
                String route;
                switch(kind){
                    case "checkin":route="/v1/management/device/checkin";fields(body,"generation event_ref student_id lesson_id attendance_revision balance_revision previous_balance_units consumed_units remaining_units occurred_at");break;
                    case "next":route="/v1/management/device/next?generation="+URLEncoder.encode(p.getString("g"),"UTF-8");fields(body,"generation");break;
                    case "receipt":route="/v1/management/device/receipt";
                        fields(body,body.optString("status").equals("applied")?"generation event_id receipt_token sha256 status attendance_revision balance_revision balance_units":"generation event_id receipt_token sha256 status conflict_code");break;
                    default:throw new IOException("route");
                }
                JSONObject response=http(p,route,kind.equals("next")?null:text.getBytes(StandardCharsets.UTF_8));
                if(!profile().getString("id").equals(profileId))throw new IOException("profile changed");
                reply(id,response);
            }catch(Exception e){reply(id,result(false,0,null));}
            finally{busy.set(false);}
        });
    }
    private void reply(String id,JSONObject response){if(!closed)activity.runOnUiThread(()->{if(!closed)callback.reply(id,response);});}
    private static JSONObject result(boolean ok,int status,JSONObject data){try{return new JSONObject().put("ok",ok).put("status",status).put("data",data==null?JSONObject.NULL:data);}catch(Exception e){return new JSONObject();}}
    private static void fields(JSONObject row,String names)throws Exception{
        java.util.Set<String> allowed=new java.util.HashSet<>(java.util.Arrays.asList(names.split(" ")));
        if(row.length()!=allowed.size())throw new IOException("fields");java.util.Iterator<String> keys=row.keys();while(keys.hasNext())if(!allowed.contains(keys.next()))throw new IOException("fields");
    }
    private static String digest(byte[] value)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(value))out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
    private JSONObject http(JSONObject p,String route,byte[] body)throws Exception{
        X509TrustManager trust=new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] c,String a)throws java.security.cert.CertificateException{throw new java.security.cert.CertificateException();}
            public void checkServerTrusted(X509Certificate[] c,String a)throws java.security.cert.CertificateException{
                try{if(c==null||c.length!=1||!pin.equals(digest(c[0].getPublicKey().getEncoded())))throw new Exception();c[0].checkValidity();c[0].verify(c[0].getPublicKey());}
                catch(Exception e){throw new java.security.cert.CertificateException("server identity");}
            }};
        SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(null,new TrustManager[]{trust},null);
        HttpsURLConnection connection=(HttpsURLConnection)new URL(p.getString("e")+route).openConnection();
        try{
            connection.setSSLSocketFactory(ssl.getSocketFactory()); // Default hostname verification retained.
            connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(8000);connection.setReadTimeout(20000);connection.setUseCaches(false);
            connection.setRequestProperty("Authorization","Bearer "+p.getString("t"));connection.setRequestProperty("Cookie","");connection.setRequestProperty("Connection","close");connection.setRequestProperty("Accept","application/json");
            if(body!=null){connection.setRequestMethod("POST");connection.setRequestProperty("Content-Type","application/json");connection.setDoOutput(true);connection.setFixedLengthStreamingMode(body.length);try(OutputStream out=connection.getOutputStream()){out.write(body);}}
            int status=connection.getResponseCode();InputStream in=status==200?connection.getInputStream():connection.getErrorStream();
            JSONObject data=in==null?new JSONObject():new JSONObject(new String(read(in,65536),StandardCharsets.UTF_8));return result(status==200,status,data);
        }finally{connection.disconnect();}
    }
    private static byte[] read(InputStream stream,int max)throws IOException{
        try(InputStream in=stream;ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1){if(out.size()+n>max)throw new IOException("limit");out.write(b,0,n);}return out.toByteArray();}
    }
    void close(){closed=true;worker.shutdownNow();}
}
