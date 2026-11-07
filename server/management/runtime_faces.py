"""Encrypted SFace profiles and server-side, lesson-scoped face matching.

Only normalized 128-value descriptors cross the browser API. Original camera
frames are never persisted by this module. AES-GCM associated data binds every
ciphertext to its organization, student and model version.
"""
import base64
import json
import math

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
        except (ValueError, TypeError):
            raise Problem(409, 'face_profile_invalid', '人脸档案无法读取，请重新录入') from None
        return validate_frames(frames)


def match_face(query_frames, candidates):
    """Return the only safe candidate; never return another student's scores."""
    ranked = []
    for candidate in candidates:
        profile_frames = candidate['frames']
        frame_scores = [max(cosine(query, saved) for saved in profile_frames)
                        for query in query_frames]
        score = math.fsum(frame_scores) / FRAME_COUNT
        ranked.append(dict(candidate, score=score, support_floor=min(frame_scores)))
    ranked.sort(key=lambda item: item['score'], reverse=True)
    if not ranked:
        raise Problem(409, 'face_profiles_missing', '本节课还没有可用的人脸档案，请先由管理员录入')
    best = ranked[0]
    second_score = ranked[1]['score'] if len(ranked) > 1 else -1.0
    margin = best['score'] - second_score
    if best['score'] < 0.52 or best['support_floor'] < 0.48:
        raise Problem(409, 'face_not_recognized', '未通过本人核验，本次没有签到或扣课时')
    if margin < 0.08:
        raise Problem(409, 'face_ambiguous', '识别结果不够唯一，请调整光线后重试或改用补签')
    return {'student_id': best['student_id'], 'student_name': best['student_name'],
            'score': round(best['score'], 6), 'margin': round(margin, 6)}
