"""Encrypted SFace profiles and server-side identity verification.

Only normalized 128-value descriptors cross the browser API. Original camera
frames are never persisted by this module. AES-GCM associated data binds every
ciphertext to its organization, student and model version.
"""
import base64
import json
import math

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from business.store import Problem


FACE_MODEL = 'sface-int8bq-v1'
FRAME_COUNT = 3
DESCRIPTOR_SIZE = 128


def validate_frames(frames):
    if not isinstance(frames, list) or len(frames) != FRAME_COUNT:
        raise Problem(422, 'invalid_face_frames', '需要连续采集 3 帧人脸特征')
    normalized = []
    for frame in frames:
        if not isinstance(frame, list) or len(frame) != DESCRIPTOR_SIZE:
            raise Problem(422, 'invalid_face_descriptor', '人脸特征必须为 128 维')
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in frame):
            raise Problem(422, 'invalid_face_descriptor', '人脸特征包含无效数值')
        norm = math.sqrt(math.fsum(float(value) ** 2 for value in frame))
        if not 0.95 <= norm <= 1.05:
            raise Problem(422, 'invalid_face_descriptor', '人脸特征未正确归一化')
        normalized.append([float(value) / norm for value in frame])
    for index in range(1, FRAME_COUNT):
        if cosine(normalized[0], normalized[index]) < 0.25:
            raise Problem(409, 'face_frames_inconsistent', '连续画面不是同一张稳定人脸，请重新采集')
    return normalized


def cosine(left, right):
    return math.fsum(a * b for a, b in zip(left, right))


class FaceCipher:
    def __init__(self, encoded_key):
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode('ascii'))
            if len(key) != 32:
                raise ValueError('wrong key length')
            self.cipher = AESGCM(key)
        except (AttributeError, UnicodeError, ValueError):
            raise Problem(503, 'face_not_configured', '服务器尚未配置人脸档案加密密钥') from None

    @staticmethod
    def associated_data(org_id, student_id):
        return '\0'.join((org_id, student_id, FACE_MODEL)).encode('utf-8')

    def encrypt(self, org_id, student_id, frames, nonce):
        if len(nonce) != 12:
            raise ValueError('AES-GCM nonce must contain 12 bytes')
        plaintext = json.dumps(frames, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        return nonce + self.cipher.encrypt(nonce, plaintext, self.associated_data(org_id, student_id))

    def decrypt(self, org_id, student_id, stored):
        raw = bytes(stored)
        if len(raw) < 29:
            raise Problem(409, 'face_profile_invalid', '人脸档案无法读取，请重新录入')
        try:
            frames = json.loads(self.cipher.decrypt(raw[:12], raw[12:], self.associated_data(org_id, student_id)))
        except (InvalidTag, ValueError, TypeError):
            raise Problem(409, 'face_profile_invalid', '人脸档案无法读取，请由校区管理员重新录入') from None
        return validate_frames(frames)


def verify_face(query_frames, profile_frames):
    """Compare the camera samples with one selected student's profile."""
    frame_scores = [max(cosine(query, saved) for saved in profile_frames) for query in query_frames]
    score = math.fsum(frame_scores) / FRAME_COUNT
    if score < 0.52 or min(frame_scores) < 0.48:
        raise Problem(409, 'face_not_recognized', '与所选学生的人脸不匹配，请确认是这位学生本人后重试')
    return round(score, 6)
