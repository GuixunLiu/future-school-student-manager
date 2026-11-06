import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.Certificate;
import java.security.spec.*;
import java.util.*;
import javax.crypto.*;
import javax.crypto.spec.*;

/** Local provisioning only. Password/token input never appears in arguments or output. */
public final class MirrorCrypto {
    static String hex(byte[] bytes) {StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString();}
    static byte[] pem(String kind,byte[] value) {return ("-----BEGIN "+kind+"-----\n"+Base64.getMimeEncoder(64,new byte[]{10}).encodeToString(value)+"\n-----END "+kind+"-----\n").getBytes(StandardCharsets.US_ASCII);}
    public static void main(String[] args) throws Exception {
        if(args.length==3&&args[0].equals("export")) {
            char[] password=System.getenv("WX_MIRROR_P12_PASSWORD").toCharArray();KeyStore store=KeyStore.getInstance("PKCS12");
            try(java.io.InputStream in=Files.newInputStream(Path.of(args[1]))){store.load(in,password);}
            PrivateKey key=(PrivateKey)store.getKey("webmirror",password);Arrays.fill(password,'\0');Certificate cert=store.getCertificate("webmirror");Path out=Path.of(args[2]);Files.createDirectories(out);
            Files.write(out.resolve("server.key"),pem("PRIVATE KEY",key.getEncoded()));Files.write(out.resolve("server.crt"),pem("CERTIFICATE",cert.getEncoded()));
            System.out.println("{\"spki_sha256\":\""+hex(MessageDigest.getInstance("SHA-256").digest(cert.getPublicKey().getEncoded()))+"\",\"certificate_sha256\":\""+hex(MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()))+"\"}");return;
        }
        if(args.length==2&&args[0].equals("wrap")) {
            PublicKey key=KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(Files.readAllBytes(Path.of(args[1]))));
            byte[] data=System.in.readNBytes(319);if(data.length>318||System.in.read()!=-1)throw new IllegalArgumentException("Connection payload exceeds RSA OAEP bound");
            Cipher cipher=Cipher.getInstance("RSA/ECB/OAEPPadding");cipher.init(Cipher.ENCRYPT_MODE,key,new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
            System.out.println(Base64.getEncoder().encodeToString(cipher.doFinal(data)));Arrays.fill(data,(byte)0);return;
        }
        throw new IllegalArgumentException("Unsupported crypto operation");
    }
}
