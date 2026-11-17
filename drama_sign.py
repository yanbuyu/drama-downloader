#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPL-3.0 — 依据 ch6vip/novel-sound 的 rust/src/sign 移植。
上游： https://github.com/ch6vip/novel-sound （2026-11-17）
采用 Python 标准库和 cryptography，所有请求签名在本进程生成。
"""
import base64
import hashlib
import secrets
import struct
import time
from urllib.parse import parse_qs
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1


def rotl(x, n, bits=64):
    m = (1 << bits) - 1
    n %= bits
    return ((x << n) | (x >> ((bits-n) % bits))) & m


def rotr(x, n, bits=64):
    return rotl(x, -n, bits)


def sm3(msg):
    """SM3：优先 OpenSSL；Windows 不提供时使用等价的纯 Python 实现。"""
    try:
        return hashlib.new('sm3', msg).digest()
    except ValueError:
        return _sm3_python(msg)


def _sm3_python(message):
    # GB/T 32905-2016 (SM3)，完整压缩函数；供不带SM3的Python发行版使用。
    mask=(1<<32)-1
    iv=[0x7380166f,0x4914b2b9,0x172442d7,0xda8a0600,
        0xa96f30bc,0x163138aa,0xe38dee4d,0xb0fb0e4e]
    padded=bytearray(message)+b'\x80'
    padded+=bytes((56-len(padded)%64)%64)
    padded+=(len(message)*8).to_bytes(8,'big')
    for offset in range(0,len(padded),64):
        buf=padded[offset:offset+64]
        w=[int.from_bytes(buf[i:i+4],'big') for i in range(0,64,4)]
        for i in range(16,68):
            x=w[i-16]^w[i-9]^rotl(w[i-3],15,32)
            p=x^rotl(x,15,32)^rotl(x,23,32)
            w.append((p^rotl(w[i-13],7,32)^w[i-6])&mask)
        ww=[w[i]^w[i+4] for i in range(64)]
        a,b,c,d,e,f,g,h=iv
        for i in range(64):
            t=0x79cc4519 if i<16 else 0x7a879d8a
            ss1=rotl((rotl(a,12,32)+e+rotl(t,i,32))&mask,7,32)
            ss2=ss1^rotl(a,12,32)
            ff=(a^b^c) if i<16 else ((a&b)|(a&c)|(b&c))
            gg=(e^f^g) if i<16 else ((e&f)|((~e)&g))
            tt1=(ff+d+ss2+ww[i])&mask
            tt2=(gg+h+ss1+w[i])&mask
            a,b,c,d=tt1,a,rotl(b,9,32),c
            x=tt2^rotl(tt2,9,32)^rotl(tt2,17,32)
            e,f,g,h=x,e,rotl(f,19,32),g
        iv=[x^y for x,y in zip(iv,[a,b,c,d,e,f,g,h])]
    return b''.join(x.to_bytes(4,'big') for x in iv)


def pad16(raw):
    n = 16 - len(raw) % 16
    return raw + bytes([n])*n


def aes_cbc_enc(data, key, iv):
    c = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return c.update(data) + c.finalize()


def aes_cbc_dec(data, key, iv):
    c = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return c.update(data) + c.finalize()


def varint32(v):
    v &= MASK32
    out = bytearray()
    while v >= 128:
        out.append((v & 127) | 128)
        v >>= 7
    out.append(v)
    return bytes(out)


class Proto:
    def __init__(self):
        self.buf = bytearray()

    def put_int(self, field, value):
        self.buf.extend(varint32(field << 3))
        self.buf.extend(varint32(value))
        return self

    def put_raw(self, field, data):
        self.buf.extend(varint32(field << 3 | 2))
        self.buf.extend(varint32(len(data)))
        self.buf.extend(data)
        return self

    def put_str(self, field, text):
        return self.put_raw(field, str(text).encode())

    def bytes(self):
        return bytes(self.buf)


def simon_enc(block, key):
    z = 0x3DC94C3A046D678B
    k = list(struct.unpack('<QQQQ', key))
    for i in range(4, 72):
        tmp = rotr(k[i-1], 3) ^ k[i-3]
        tmp ^= rotr(tmp, 1)
        k.append(((~k[i-4]) ^ tmp ^ ((z >> ((i-4)%62)) & 1) ^ 3) & MASK64)
    x0,x1 = struct.unpack('<QQ',block)
    for r in k:
        x0,x1 = x1,(x0 ^ (rotl(x1,1) & rotl(x1,8)) ^ rotl(x1,2) ^ r) & MASK64
    return struct.pack('<QQ',x0,x1)


ARGUS_SIGN_KEY=bytes.fromhex('fc78e0a9657a0c748ce51559903ccf03510e51d3cff232d71343e88a321c5304')
ARGUS_AES_KEY=bytes.fromhex('d31e3718288a1027baab59f146a09a9c')
ARGUS_AES_IV=bytes.fromhex('ea180a0336ed352fcd24e4d50018ae54')


def argus(query, body=b'', ts=None, random_i32=None, install_id=''):
    """新版 551 Argus，含 SM3/SIMON/AES。"""
    ts = int(time.time()) if ts is None else ts
    rand = secrets.randbits(32) if random_i32 is None else random_i32
    q = parse_qs(query, keep_blank_values=True)
    aid = int(q.get('aid',['1967'])[0])
    device_id = q.get('device_id',[''])[0]
    version = q.get('version_name',[''])[0]
    license_id = 1611921764
    empty = bytes(16)
    body_hash = sm3(hashlib.md5(body).digest() if body else empty)[:6]
    query_hash = sm3(query.encode() if query else empty)[:6]
    b = Proto()
    b.put_int(1, 1077940818).put_int(2, 2).put_int(3,rand).put_str(4,str(aid))
    b.put_str(5,device_id).put_str(6,str(license_id)).put_str(7,version)
    b.put_str(8,'v04.04.05-ov-android').put_int(9,134744640)
    b.put_raw(10,bytes(8)).put_int(11,0).put_int(12,ts << 1)
    b.put_raw(13,body_hash).put_raw(14,query_hash)
    sub = Proto().put_int(1,1).put_int(2,1).put_int(3,1).put_int(7,0xC792ECCC)
    b.put_raw(15,sub.bytes()).put_str(16,install_id).put_str(20,'none').put_int(21,738)
    sub=Proto().put_str(1,'NX551J').put_int(2,8196).put_int(4,0x80E0D800)
    b.put_raw(23,sub.bytes()).put_int(25,2)
    plain=pad16(b.bytes())
    encrypted = b''.join(simon_enc(plain[i:i+16],ARGUS_SIGN_KEY) for i in range(0,len(plain),16))
    magic=bytes.fromhex('f2f7fcfff2f7fcff')
    tail=bytes.fromhex('eafb2cfe8568519154')
    plain=magic+encrypted+tail
    buf=(magic+bytes(x ^ magic[i%8] for i,x in enumerate(plain[8:], start=8)))[::-1]+b'ao'
    return base64.b64encode(b'\xf2\x81'+aes_cbc_enc(pad16(buf),ARGUS_AES_KEY,ARGUS_AES_IV)).decode()


def ladon(khronos, license_id=1611921764, aid=1967, random4=None):
    random4 = secrets.token_bytes(4) if random4 is None else random4
    text=f'{khronos}-{license_id}-{aid}'.encode()
    md5hex=hashlib.md5(random4+str(aid).encode()).hexdigest().encode()
    table = bytearray(288)
    table[:32]=md5hex
    words=list(struct.unpack('<QQQQ',table[:32]))
    b0,b8=words[:2]
    queue=words[2:]
    for i in range(34):
        x9=b0
        x8=(rotr(b8,8)+x9)&MASK64
        x8 ^= i
        queue.append(x8)
        x8 ^= rotl(x9,3)
        table[(i+1)*8:(i+2)*8]=struct.pack('<Q',x8)
        b0,b8=x8,queue.pop(0)
    output=bytearray()
    source=pad16(text)
    for offset in range(0,len(source),16):
        d0,d1=struct.unpack('<QQ',source[offset:offset+16])
        for i in range(34):
            h=struct.unpack('<Q',table[i*8:i*8+8])[0]
            d1=h ^ ((d0+rotr(d1,8)) & MASK64)
            d0=d1 ^ rotl(d0,3)
        output+=struct.pack('<QQ',d0,d1)
    return base64.b64encode(random4+output).decode()


def sign_headers(query, body=None, *, gorgon=None, install_id='', ts=None, nonce=None):
    ts = int(time.time()) if ts is None else int(ts)
    aid=int(parse_qs(query).get('aid',['1967'])[0])
    data=body or b''
    headers={
      'x-ss-req-ticket': str(ts*1000), 'x-khronos':str(ts),
      'x-ladon':ladon(ts,aid=aid,random4=nonce),
      'x-argus':argus(query,data,ts=ts,install_id=install_id),
    }
    if data: headers['x-ss-stub']=hashlib.md5(data).hexdigest()
    if gorgon:
        headers['x-gorgon']=gorgon(query)
    return headers
