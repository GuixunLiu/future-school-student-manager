package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.io.*;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.spec.MGF1ParameterSpec;
import java.security.cert.X509Certificate;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.KeyGenerator;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.*;

/** Fixed-route pinned client. No token enters JavaScript; no auto-enrolment.
 * Explicit migration tooling must provision a Keystore-encrypted native profile.
 * Existing mirror credentials are never silently reused for canonical writes.
 */
final class CanonicalSyncTransport {
    interface Callback { void reply(String id, JSONObject response); }
    static final String PREFS="canonical_sync_native_v1", AES="school_canonical_sync_aes_v1", RSA="school_canonical_sync_rsa_v1";
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
    private JSONObject profile()throws Exception{return profile(true);}
    private JSONObject profile(boolean requireEnabled)throws Exception{
        if(requireEnabled&&(!prefs.getBoolean("enabled",false)||prefs.getBoolean("activation_pending",false)))throw new IOException("disabled");
        KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);
        SecretKey key=(SecretKey)keys.getKey(AES,null);if(key==null)throw new IOException("unprovisioned");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),2)));
        JSONObject p=new JSONObject(new String(cipher.doFinal(Base64.decode(prefs.getString("secret",""),2)),StandardCharsets.UTF_8));
        validate(p);return p;
    }
    private void validate(JSONObject p)throws Exception{
        fields(p,"e t d p o g id b n x");
        if(!pin.matches("[a-f0-9]{64}")||!pin.equals(p.getString("p"))||!p.getString("t").matches("wxd_[A-Za-z0-9_-]{43}"))throw new IOException("profile");
        for(String k:new String[]{"o","d","g","id"})if(!p.getString(k).matches("[a-f0-9]{32}"))throw new IOException("profile");
        if(!p.getString("b").matches("[a-f0-9]{64}")||!p.getString("n").matches("[a-f0-9]{32}")||p.getLong("x")<=System.currentTimeMillis()/1000)throw new IOException("baseline or expiry");
        String endpoint=p.getString("e");
        if(!endpoint.equals("https://47.97.226.219:18083")&&!(BuildConfig.DEBUG&&endpoint.matches("https://127\\.0\\.0\\.1:[0-9]{4,5}")))throw new IOException("endpoint");
    }
    String overview(){
        if(prefs.getBoolean("activation_pending",false))try{JSONObject p=profile(false);return new JSONObject().put("configured",false).put("staged",true).put("activationPending",true).put("orgId",p.getString("o")).put("deviceId",p.getString("d")).put("generation",p.getString("g")).put("profileId",p.getString("id")).put("baselineSha256",p.getString("b")).toString();}catch(Exception ignored){}
        if(!prefs.getBoolean("enabled",false)&&prefs.getBoolean("staged",false))try{JSONObject p=profile(false);return new JSONObject().put("configured",false).put("staged",true).put("orgId",p.getString("o")).put("deviceId",p.getString("d")).put("generation",p.getString("g")).put("profileId",p.getString("id")).put("baselineSha256",p.getString("b")).toString();}catch(Exception ignored){}
        try{JSONObject p=profile();return new JSONObject().put("configured",true).put("orgId",p.getString("o")).put("deviceId",p.getString("d"))
            .put("generation",p.getString("g")).put("profileId",p.getString("id")).toString();}catch(Exception e){return "{\"configured\":false}";}
    }
    private KeyStore keys()throws Exception{KeyStore k=KeyStore.getInstance("AndroidKeyStore");k.load(null);return k;}
    private PrivateKey rsa()throws Exception{
        if(!keys().containsAlias(RSA)){KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA","AndroidKeyStore");generator.initialize(new KeyGenParameterSpec.Builder(RSA,KeyProperties.PURPOSE_DECRYPT).setKeySize(3072).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP).setDigests(KeyProperties.DIGEST_SHA256,KeyProperties.DIGEST_SHA1).build());generator.generateKeyPair();}
        return (PrivateKey)keys().getKey(RSA,null);
    }
    synchronized void prepareEnrollment(){
        if(closed)return;worker.execute(()->{try{
            rsa();String nonce=prefs.getString("nonce","");if(nonce.isEmpty()){nonce=java.util.UUID.randomUUID().toString().replace("-","");if(!prefs.edit().putString("nonce",nonce).commit())throw new IOException();}
            JSONObject request=new JSONObject().put("protocol",1).put("nonce",nonce).put("public_key",Base64.encodeToString(keys().getCertificate(RSA).getPublicKey().getEncoded(),2));
            File root=activity.getExternalFilesDir(null);if(root==null)throw new IOException();
            android.util.AtomicFile file=new android.util.AtomicFile(new File(root,"canonical-sync-request.json"));FileOutputStream out=file.startWrite();try{out.write(request.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);}catch(Exception e){file.failWrite(out);throw e;}
            reply("enrollment",result(true,200,new JSONObject().put("prepared",true)));
        }catch(Exception e){reply("enrollment",result(false,0,null));}});
    }
    void stageEnrollment(String baseline){
        if(closed||baseline==null||!baseline.matches("[a-f0-9]{64}"))return;
        activity.runOnUiThread(()->new AlertDialog.Builder(activity).setTitle("核对新的同步连接")
            .setMessage("将读取已核对资料对应的加密连接，仅准备新连接；日常同步模式暂不切换。")
            .setNegativeButton("取消",null).setPositiveButton("核对连接",(dialog,which)->stage(baseline)).show());
    }
    void captureEnrollment(String raw,String baseline,long capturedAt){
        if(closed||raw==null||raw.length()>20*1024*1024)return;
        activity.runOnUiThread(()->new AlertDialog.Builder(activity).setTitle("采集当前核对资料")
            .setMessage("将在应用本机目录保存完整资料包，含学生私有资料。仅供本次迁移核对，不会上传、扣课或切换同步；请勿公开分享。")
            .setNegativeButton("取消",null).setPositiveButton("保存核对包",(dialog,which)->capture(raw,baseline,capturedAt)).show());
    }
    synchronized void capture(String raw,String baseline,long capturedAt){
        if(closed)return;
        if(!busy.compareAndSet(false,true)){reply("capture",result(false,429,null));return;}
        worker.execute(()->{try{
            long now=System.currentTimeMillis()/1000;
            if(prefs.getBoolean("enabled",false)||raw==null||raw.length()>20*1024*1024||baseline==null||!baseline.matches("[a-f0-9]{64}")||capturedAt<0||capturedAt>now+120||now-capturedAt>=120)throw new IOException("capture");
            byte[] bytes=raw.getBytes(StandardCharsets.UTF_8);
            if(bytes.length>20*1024*1024||!digest(bytes).equals(baseline))throw new IOException("capture digest");
            JSONObject data=new JSONObject(raw);
            for(String key:new String[]{"classes","students","attendance","transactions"})data.getJSONArray(key);
            data.getJSONObject("settings");
            JSONObject output=new JSONObject().put("format","future-school-native-capture-v1").put("captured_at",capturedAt)
                .put("sha256",baseline).put("bytes",bytes.length).put("payload_base64",Base64.encodeToString(bytes,2));
            File root=activity.getExternalFilesDir(null);if(root==null)throw new IOException("storage");
            String name="canonical-capture-"+java.util.UUID.randomUUID().toString().replace("-","")+".private.json";
            android.util.AtomicFile file=new android.util.AtomicFile(new File(root,name));FileOutputStream out=file.startWrite();
            try{out.write(output.toString().getBytes(StandardCharsets.UTF_8));file.finishWrite(out);}catch(Exception e){file.failWrite(out);throw e;}
            // No private payload, names, contact details or tokens in callbacks.
            reply("capture",result(true,200,new JSONObject().put("file_name",name).put("sha256",baseline).put("bytes",bytes.length).put("captured_at",capturedAt)));
        }catch(Exception e){reply("capture",result(false,0,null));}finally{busy.set(false);}});
    }
    synchronized void stage(String baseline){
        if(closed||baseline==null||!baseline.matches("[a-f0-9]{64}")||!busy.compareAndSet(false,true))return;
        worker.execute(()->{try{
            if(prefs.getBoolean("enabled",false))throw new IOException("already active");
            File root=activity.getExternalFilesDir(null);if(root==null)throw new IOException();
            JSONObject envelope=new JSONObject(new String(read(new FileInputStream(new File(root,"canonical-sync-connection.enc.json")),16384),StandardCharsets.UTF_8));
            fields(envelope,"protocol nonce wrapped_key iv ciphertext");String nonce=prefs.getString("nonce","");
            if(envelope.getInt("protocol")!=1||nonce.isEmpty()||!nonce.equals(envelope.getString("nonce")))throw new IOException("different device");
            Cipher unwrap=Cipher.getInstance("RSA/ECB/OAEPPadding");unwrap.init(Cipher.DECRYPT_MODE,rsa(),new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
            byte[] session=unwrap.doFinal(Base64.decode(envelope.getString("wrapped_key"),2));byte[] iv=Base64.decode(envelope.getString("iv"),2);
            if(session.length!=32||iv.length!=12)throw new IOException("envelope");
            Cipher decrypt=Cipher.getInstance("AES/GCM/NoPadding");decrypt.init(Cipher.DECRYPT_MODE,new SecretKeySpec(session,"AES"),new GCMParameterSpec(128,iv));decrypt.updateAAD(("future-school-canonical-v1:"+nonce).getBytes(StandardCharsets.UTF_8));
            JSONObject p=new JSONObject(new String(decrypt.doFinal(Base64.decode(envelope.getString("ciphertext"),2)),StandardCharsets.UTF_8));validate(p);
            if(!p.getString("n").equals(nonce)||!p.getString("b").equals(baseline))throw new IOException("baseline");
            JSONObject previous=null;if(prefs.getBoolean("staged",false))try{previous=profile(false);}catch(Exception ignored){}
            if(previous!=null&&!previous.getString("id").equals(p.getString("id")))throw new IOException("pending profile differs");
            JSONObject response=http(p,"/v1/management/device/plan?generation="+URLEncoder.encode(p.getString("g"),"UTF-8"),null);
            if(!response.getBoolean("ok"))throw new IOException("authorization");JSONObject plan=response.getJSONObject("data");
            if(plan.getInt("protocol")!=1||!plan.getString("mode").equals("canonical_enrollment_staging")||plan.getBoolean("enabled")||!plan.getBoolean("activation_required"))throw new IOException("plan");
            for(String[] pair:new String[][]{{"org_id","o"},{"device_id","d"},{"generation","g"},{"profile_id","id"},{"nonce","n"},{"baseline_sha256","b"}})if(!plan.getString(pair[0]).equals(p.getString(pair[1])))throw new IOException("plan identity");
            if(plan.getLong("expires_at")!=p.getLong("x"))throw new IOException("plan expiry");
            if(!keys().containsAlias(AES)){KeyGenerator generator=KeyGenerator.getInstance("AES","AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(AES,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());generator.generateKey();}
            Cipher save=Cipher.getInstance("AES/GCM/NoPadding");save.init(Cipher.ENCRYPT_MODE,(SecretKey)keys().getKey(AES,null));String secret=Base64.encodeToString(save.doFinal(p.toString().getBytes(StandardCharsets.UTF_8)),2);
            if(!prefs.edit().putString("secret",secret).putString("iv",Base64.encodeToString(save.getIV(),2)).putBoolean("staged",true).putBoolean("enabled",false).commit())throw new IOException("save");
            reply("enrollment",result(true,200,new JSONObject().put("staged",true).put("activated",false)));
        }catch(Exception e){reply("enrollment",result(false,0,null));}finally{busy.set(false);}});
    }
    private File activationFile()throws IOException{File root=activity.getFilesDir();if(root==null)throw new IOException("storage");return new File(root,"canonical-activation-staged.enc");}
    private void saveActivation(JSONObject value)throws Exception{
        Cipher save=Cipher.getInstance("AES/GCM/NoPadding");save.init(Cipher.ENCRYPT_MODE,(SecretKey)keys().getKey(AES,null));
        byte[] bytes=save.doFinal(value.toString().getBytes(StandardCharsets.UTF_8));
        android.util.AtomicFile file=new android.util.AtomicFile(activationFile());FileOutputStream out=file.startWrite();
        try{out.write(save.getIV());out.write(bytes);file.finishWrite(out);}catch(Exception e){file.failWrite(out);throw e;}
    }
    private JSONObject loadActivation()throws Exception{
        if(!prefs.getBoolean("activation_ready",false))throw new IOException("not prepared");
        byte[] bytes=read(new android.util.AtomicFile(activationFile()).openRead(),20*1024*1024);
        if(bytes.length<29)throw new IOException("activation storage");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,(SecretKey)keys().getKey(AES,null),new GCMParameterSpec(128,java.util.Arrays.copyOfRange(bytes,0,12)));
        return new JSONObject(new String(cipher.doFinal(java.util.Arrays.copyOfRange(bytes,12,bytes.length)),StandardCharsets.UTF_8));
    }
    String activationBootstrap(){try{return loadActivation().getJSONObject("payload").toString();}catch(Exception e){return "{}";}}
    private JSONObject activationPlan(JSONObject p,JSONObject payload)throws Exception{
        JSONObject response=http(p,"/v1/management/device/activation?generation="+p.getString("g")+"&profile_id="+p.getString("id"),null);
        if(!response.getBoolean("ok"))throw new IOException("activation authorization");JSONObject plan=response.getJSONObject("data");
        long now=System.currentTimeMillis()/1000;
        if(plan.getInt("protocol")!=1||!plan.getString("mode").equals("canonical_activation")||plan.getBoolean("old_mirror_writable")||!plan.getString("activation_id").matches("[a-f0-9]{32}")||!plan.getString("nonce").matches("[a-f0-9]{32}")||!java.util.Arrays.asList("offered","active").contains(plan.getString("phase"))||plan.getLong("active_until")<=now||plan.getLong("active_until")>now+31L*86400)throw new IOException("activation plan");
        if(plan.getString("phase").equals("offered")&&(plan.getLong("offer_expires_at")<=now||plan.getLong("offer_expires_at")>now+900))throw new IOException("activation offer expired");
        for(String[] pair:new String[][]{{"org_id","o"},{"device_id","d"},{"generation","g"},{"profile_id","id"},{"baseline_sha256","b"}})if(!plan.getString(pair[0]).equals(p.getString(pair[1]))||!payload.getString(pair[0]).equals(p.getString(pair[1])))throw new IOException("activation identity");
        for(String key:new String[]{"bootstrap_sha256","source_map_sha256"})if(!plan.getString(key).matches("[a-f0-9]{64}")||!plan.getString(key).equals(payload.getString(key)))throw new IOException("activation digest");
        return plan;
    }
    void prepareActivation(String currentSha){
        if(closed)return;activity.runOnUiThread(()->new AlertDialog.Builder(activity).setTitle("启用网页主管理")
            .setMessage("将核对完整资料及新连接，保存成功后由网页管理学生、班级和课时；平板继续负责签到。")
            .setNegativeButton("取消",null).setPositiveButton("核对并启用",(dialog,which)->prepareActivationNow(currentSha)).show());
    }
    synchronized void prepareActivationNow(String currentSha){
        if(closed||!busy.compareAndSet(false,true))return;
        worker.execute(()->{try{
            JSONObject p=profile(false);File root=activity.getExternalFilesDir(null);if(root==null)throw new IOException("storage");
            JSONObject envelope=new JSONObject(new String(read(new FileInputStream(new File(root,"canonical-bootstrap.enc.json")),20*1024*1024),StandardCharsets.UTF_8));
            fields(envelope,"protocol nonce wrapped_key iv ciphertext");String nonce=prefs.getString("nonce","");
            if(envelope.getInt("protocol")!=1||!nonce.equals(envelope.getString("nonce"))||!nonce.equals(p.getString("n")))throw new IOException("bootstrap device");
            Cipher unwrap=Cipher.getInstance("RSA/ECB/OAEPPadding");unwrap.init(Cipher.DECRYPT_MODE,rsa(),new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
            byte[] session=unwrap.doFinal(Base64.decode(envelope.getString("wrapped_key"),2)),iv=Base64.decode(envelope.getString("iv"),2);
            if(session.length!=32||iv.length!=12)throw new IOException("bootstrap envelope");
            Cipher decrypt=Cipher.getInstance("AES/GCM/NoPadding");decrypt.init(Cipher.DECRYPT_MODE,new SecretKeySpec(session,"AES"),new GCMParameterSpec(128,iv));decrypt.updateAAD(("future-school-canonical-bootstrap-v1:"+nonce).getBytes(StandardCharsets.UTF_8));
            JSONObject payload=new JSONObject(new String(decrypt.doFinal(Base64.decode(envelope.getString("ciphertext"),2)),StandardCharsets.UTF_8));
            fields(payload,"format org_id device_id generation profile_id baseline_sha256 source_map_sha256 bootstrap_sha256 snapshot_base64");
            if(!payload.getString("format").equals("future-school-canonical-bootstrap-v1"))throw new IOException("bootstrap format");
            byte[] snapshot=Base64.decode(payload.getString("snapshot_base64"),2);
            if(snapshot.length>10*1024*1024||!digest(snapshot).equals(payload.getString("bootstrap_sha256"))||currentSha==null||(!currentSha.equals(p.getString("b"))&&!currentSha.equals(payload.getString("bootstrap_sha256"))))throw new IOException("local baseline");
            JSONObject plan=activationPlan(p,payload);saveActivation(new JSONObject().put("payload",payload).put("plan",plan));
            if(!prefs.edit().putBoolean("activation_ready",true).commit())throw new IOException("save bootstrap");
            reply("activation",result(true,200,new JSONObject().put("prepared",true)));
        }catch(Exception e){reply("activation",result(false,0,null));}finally{busy.set(false);}});
    }
    private void activationProfile(JSONObject p)throws Exception{
        Cipher save=Cipher.getInstance("AES/GCM/NoPadding");save.init(Cipher.ENCRYPT_MODE,(SecretKey)keys().getKey(AES,null));
        String secret=Base64.encodeToString(save.doFinal(p.toString().getBytes(StandardCharsets.UTF_8)),2);
        if(!prefs.edit().putString("secret",secret).putString("iv",Base64.encodeToString(save.getIV(),2)).putBoolean("enabled",true).putBoolean("staged",true).putBoolean("activation_pending",true).commit())throw new IOException("save activation profile");
    }
    synchronized void commitActivation(String savedSha){
        if(closed||!busy.compareAndSet(false,true))return;
        worker.execute(()->{try{
            JSONObject stored=loadActivation(),payload=stored.getJSONObject("payload"),p=profile(false);
            if(savedSha==null||!savedSha.equals(payload.getString("bootstrap_sha256")))throw new IOException("saved snapshot");
            JSONObject plan=activationPlan(p,payload);p.put("x",plan.getLong("active_until"));activationProfile(p);
            JSONObject body=new JSONObject().put("generation",p.getString("g")).put("activation_id",plan.getString("activation_id")).put("profile_id",p.getString("id")).put("nonce",plan.getString("nonce")).put("bootstrap_sha256",savedSha).put("source_map_sha256",payload.getString("source_map_sha256"));
            JSONObject response=http(p,"/v1/management/device/activation-commit",body.toString().getBytes(StandardCharsets.UTF_8));
            if(!response.getBoolean("ok"))throw new IOException("activation commit");JSONObject receipt=response.getJSONObject("data");
            for(String key:new String[]{"generation","activation_id","profile_id","bootstrap_sha256","source_map_sha256"})if(!receipt.getString(key).equals(body.getString(key)))throw new IOException("activation receipt");
            if(receipt.getInt("protocol")!=1||!receipt.getString("org_id").equals(p.getString("o"))||!receipt.getString("device_id").equals(p.getString("d"))||!receipt.getString("phase").equals("active")||receipt.getBoolean("old_mirror_writable")||receipt.getBoolean("balances_changed")||receipt.getLong("active_until")!=p.getLong("x"))throw new IOException("activation receipt state");
            if(!prefs.edit().putBoolean("activation_pending",false).putBoolean("staged",false).commit())throw new IOException("save activation receipt");
            reply("activation",result(true,200,new JSONObject().put("activated",true)));
        }catch(Exception e){reply("activation",result(false,0,null));}finally{busy.set(false);}});
    }
    synchronized void request(String id,String profileId,String kind,String text){
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
                    case "next":route="/v1/management/device/next?generation="+URLEncoder.encode(p.getString("g"),"UTF-8")+deferredQuery(body);break;
                    case "class-ready":route="/v1/management/device/class-ready";fields(body,"generation");break;
                    case "history-ready":route="/v1/management/device/history-ready";fields(body,"generation");break;
                    case "class-next":route="/v1/management/device/class-next?generation="+URLEncoder.encode(p.getString("g"),"UTF-8")+deferredQuery(body);break;
                    case "class-receipt":route="/v1/management/device/class-receipt";fields(body,"generation class_id revision receipt_token sha256 status");break;
                    case "receipt":route="/v1/management/device/receipt";
                        fields(body,body.optString("status").equals("applied")?"generation event_id receipt_token sha256 status attendance_revision balance_revision balance_units":"generation event_id receipt_token sha256 status conflict_code");break;
                    default:throw new IOException("route");
                }
                JSONObject response=http(p,route,kind.equals("next")||kind.equals("class-next")?null:text.getBytes(StandardCharsets.UTF_8));
                if(!profile().getString("id").equals(profileId))throw new IOException("profile changed");
                reply(id,response);
            }catch(Exception e){reply(id,result(false,0,null));}
            finally{busy.set(false);}
        });
    }
    private void reply(String id,JSONObject response){if(!closed)activity.runOnUiThread(()->{if(!closed)callback.reply(id,response);});}
    private static JSONObject result(boolean ok,int status,JSONObject data){try{return new JSONObject().put("ok",ok).put("status",status).put("data",data==null?JSONObject.NULL:data);}catch(Exception e){return new JSONObject();}}
    private static String deferredQuery(JSONObject body)throws Exception{
        if(!body.has("defer")){fields(body,"generation");return "";}
        fields(body,"generation defer");
        String value=body.getString("defer");
        if(!value.matches("[A-Za-z0-9_-]{1,80}(,[A-Za-z0-9_-]{1,80}){0,39}"))throw new IOException("defer");
        return "&defer="+value;
    }
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
    synchronized void close(){closed=true;worker.shutdownNow();}
}
