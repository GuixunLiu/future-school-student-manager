import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.*;
import java.util.Base64;
import javax.crypto.*;
import javax.crypto.spec.*;

/** Local-only hybrid encryption helper; plaintext profile is read from stdin.
 * No credentials in command arguments, output is ciphertext only.
 */
public final class CanonicalEnvelope {
    static byte[][] seal(PublicKey publicKey,String nonce,byte[] plain)throws Exception{
        return seal(publicKey,nonce,plain,"future-school-canonical-v1:",8192);
    }
    static byte[][] seal(PublicKey publicKey,String nonce,byte[] plain,String context,int limit)throws Exception{
        if(!(publicKey instanceof RSAPublicKey)||((RSAPublicKey)publicKey).getModulus().bitLength()<3072||!nonce.matches("[a-f0-9]{32}")||plain.length>limit)throw new Exception("input");
        KeyGenerator generator=KeyGenerator.getInstance("AES");generator.init(256);SecretKey session=generator.generateKey();
        byte[] iv=new byte[12];new SecureRandom().nextBytes(iv);
        Cipher content=Cipher.getInstance("AES/GCM/NoPadding");content.init(Cipher.ENCRYPT_MODE,session,new GCMParameterSpec(128,iv));content.updateAAD((context+nonce).getBytes(StandardCharsets.UTF_8));
        Cipher wrap=Cipher.getInstance("RSA/ECB/OAEPPadding");wrap.init(Cipher.ENCRYPT_MODE,publicKey,new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
        return new byte[][]{wrap.doFinal(session.getEncoded()),iv,content.doFinal(plain)};
    }
    static byte[] open(PrivateKey privateKey,String nonce,byte[][] sealed)throws Exception{
        Cipher unwrap=Cipher.getInstance("RSA/ECB/OAEPPadding");unwrap.init(Cipher.DECRYPT_MODE,privateKey,new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
        Cipher content=Cipher.getInstance("AES/GCM/NoPadding");content.init(Cipher.DECRYPT_MODE,new SecretKeySpec(unwrap.doFinal(sealed[0]),"AES"),new GCMParameterSpec(128,sealed[1]));content.updateAAD(("future-school-canonical-v1:"+nonce).getBytes(StandardCharsets.UTF_8));return content.doFinal(sealed[2]);
    }
    static void selftest()throws Exception{
        KeyPairGenerator g=KeyPairGenerator.getInstance("RSA");g.initialize(3072);KeyPair pair=g.generateKeyPair();String nonce="a".repeat(32);
        byte[] plain=("fictional-only-"+"x".repeat(4000)).getBytes(StandardCharsets.UTF_8);byte[][] sealed=seal(pair.getPublic(),nonce,plain);
        if(!java.util.Arrays.equals(plain,open(pair.getPrivate(),nonce,sealed)))throw new Exception("roundtrip");
        boolean rejected=false;try{open(pair.getPrivate(),"b".repeat(32),sealed);}catch(GeneralSecurityException e){rejected=true;}if(!rejected)throw new Exception("nonce accepted");
        KeyPair other=g.generateKeyPair();rejected=false;try{open(other.getPrivate(),nonce,sealed);}catch(GeneralSecurityException e){rejected=true;}if(!rejected)throw new Exception("other device accepted");
        sealed[2][0]^=1;rejected=false;try{open(pair.getPrivate(),nonce,sealed);}catch(GeneralSecurityException e){rejected=true;}if(!rejected)throw new Exception("tamper accepted");
        System.out.println("Hybrid envelope passed: long fictional payload, nonce binding, other device and tamper rejection.");
    }
    public static void main(String[] args)throws Exception{
        if(args.length==1&&args[0].equals("selftest")){selftest();return;}
        boolean bootstrap=args.length==3&&args[0].equals("--bootstrap");int offset=bootstrap?1:0;
        if(args.length!=2+offset)throw new IllegalArgumentException("[--bootstrap] public-key.der nonce; plaintext JSON on stdin");
        byte[] der=Files.readAllBytes(Path.of(args[offset]));if(der.length>2048)throw new Exception("key size");
        int limit=bootstrap?14*1024*1024:8192;byte[] plain=System.in.readNBytes(limit+1);
        byte[][] values=seal(KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(der)),args[1+offset],plain,bootstrap?"future-school-canonical-bootstrap-v1:":"future-school-canonical-v1:",limit);
        Base64.Encoder encoder=Base64.getEncoder();System.out.print("{\"protocol\":1,\"nonce\":\""+args[1+offset]+"\",\"wrapped_key\":\""+encoder.encodeToString(values[0])+"\",\"iv\":\""+encoder.encodeToString(values[1])+"\",\"ciphertext\":\""+encoder.encodeToString(values[2])+"\"}");
    }
}
