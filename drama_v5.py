#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v04.10.03 短剧播放签名纯 Python 移植 (GPL-3.0)。
源: ch6vip/novel-sound rust/src/sign/v5.rs + v5_tables.rs。
无本地签名服务、无 Frida、无 Rust 运行时。
"""
import base64
import hashlib
import secrets
import struct
import time
from urllib.parse import quote

from drama_sign import MASK32, rotl, rotr, ladon, sm3
from drama_v5_tables import HASH_TABLE, SBOX

APP_ID = '8662'
VERSION_CODE = '74332'
VERSION_NAME = '7.4.3.32'
CHANNEL = 'vivo_8662_64'
DEVICE_TYPE = 'PGT-AN10'
OS_VERSION = '12'
LICENSE = 646637794
USER_AGENT = 'com.phoenix.read/73932 (Linux; U; Android 12; zh_CN_#Hans; PGT-AN10; Build/V417IR;tt-ok/3.12.13.20)'
MAGIC = bytes.fromhex('2d4b4fca49750d433fb5ae2c226dcc56')
SIGN_KEY = bytes.fromhex('b23c2a470a20d105dc3f8c4438f8272ed46d6c89ffdcde dd5108698d26e9489d'.replace(' ',''))
INITIAL = (0x7aba4fc8,0x67166507,0x6403fa00,0x340f512f,984304912,3005047866,2874125293,2152413264)
ROUNDS = (96,1,3,4,0,6,5,2,0,7,7)
_PROCESS_NONCE = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('=')
_PROCESS_STARTED = int(time.time()*1000)


def u32(x): return x & MASK32

def le32(x): return struct.pack('<I',x&MASK32)


def vint(v):
    out=bytearray()
    while v>127:
        out.append((v&127)|128);v>>=7
    out.append(v)
    return bytes(out)


def zigzag(v): return 2*v if v>=0 else -2*v-1


def proto(*rows):
    result=bytearray()
    for tag,val,typ in rows:
        if typ=='i':
            result+=vint(tag<<3)+vint(zigzag(val))
        elif typ=='b':
            if val:
                result+=vint((tag<<3)|2)+vint(len(val))+val
        elif typ=='s':
            val=val.encode()
            if val:
                result+=vint((tag<<3)|2)+vint(len(val))+val
        else: raise ValueError(typ)
    return bytes(result)


def p_i(tag,v): return tag,v,'i'
def p_s(tag,v): return tag,v,'s'
def p_b(tag,v): return tag,v,'b'


def get_iv(initial,data):
    value=initial
    for i,b in enumerate(data):
        if i%2==0: value=u32((value>>4)^value^u32(value<<6)^b)
        else: value=u32(~((value>>7)^value^(b|u32(value<<12))))
    return value


def branch_iv(qhash, body_md5, ts):
    return get_iv(get_iv(get_iv(0x20230928,qhash),body_md5),le32(ts))


def sum_md5(data):
    check=0x20220420
    for i,b in enumerate(data[:12]):
        shift=3 if i%2==0 else 5
        temp=(check>>shift)^check
        if i%2==0: check=b^u32(check<<7)
        else: check=u32(~(b|u32(check<<11)))
        check=u32(check^temp)
    return u32((check|4)^0x01000000)


def hash_f13_v5(qhash, body_md5, ts):
    seed=0x9d4a97c2
    count=u32(seed+ts)
    table=[rotr(v,u32(count+1)&255,32) for v in HASH_TABLE]
    shift=u32(count+2)&7
    pad=bytes((((v|(v<<8))>>shift)&255) for v in [0xfa,0x45,0x61,0xd7])
    data=qhash+body_md5+le32(ts)+pad+bytes.fromhex('00000000000001a0')
    words=list(struct.unpack('>16I',data))
    for i in range(112):
        x,y=words[i+1],words[i+14]
        words.append(u32(words[i]+words[i+9]+(rotl(x,14,32)^rotl(x,25,32)^(x>>3))+(rotl(y,13,32)^rotl(y,15,32)^(y>>10))))
    initial=[rotr(v,count,32) for v in INITIAL]
    state=initial[:]
    for i in range(ROUNDS[0]):
        base=u32(seed+i)
        n1=((state[ROUNDS[3]]^state[ROUNDS[4]])&state[ROUNDS[1]])^state[ROUNDS[3]]
        x=state[ROUNDS[1]]
        n2=rotl(x,26,32)^rotl(x,21,32)^rotl(x,7,32)
        n4=u32(words[base&127]+n1+n2+table[base&63]+state[ROUNDS[5]])
        y=state[ROUNDS[2]]
        n5=rotl(y,30,32)^rotl(y,19,32)^rotl(y,10,32)
        n6=(y&state[ROUNDS[6]])|((y|state[ROUNDS[6]])&state[ROUNDS[7]])
        old=state[ROUNDS[9]]
        state=[state[-1]]+state[:-1]
        state[ROUNDS[10]]=u32(n5+n6+n4)
        state[ROUNDS[8]]=u32(old+n4)
    expanded=b''.join(struct.pack('>I',u32(v+initial[i])) for i,v in enumerate(state))
    folded=bytes(expanded[i]^expanded[i+16] for i in range(16))
    return folded+le32(sum_md5(folded)|0x20)


def xmxor(data,key):
    n=len(data)
    encoded=bytearray(n)
    for index,val in enumerate(data):
        at=(index*4)&28
        d0,d1=key[at],key[at+1]
        mixed=((val<<4)|(val>>4))&255
        mixed=u32(mixed+d0)&255
        mixed=(~mixed)^d1
        masked=mixed&255
        mixed=((((masked<<3)|(masked>>5))&255)+d1)&255
        mixed=(mixed^d0)&255
        encoded[n-index-1]=(~mixed)&255
    last=encoded[n-1]^encoded[n-2]
    first=encoded[0]
    encoded[0]=((~last)+first)&255
    encoded[1]=((encoded[0]^encoded[n-1]^0xfe)+encoded[1])&255
    encoded[2]=(encoded[2]+((last-first)&255^rotl(encoded[1],3,8)^2))&255
    for i in range(n-4):
        tmp=rotl(encoded[i+2],3,8)^encoded[i+1]^((i+3)&255)
        encoded[i+3]=((~tmp)+encoded[i+3])&255
    encoded[n-1]^=encoded[n-2]
    total=sum(encoded[1:])&255
    encoded[0]=((encoded[0]^encoded[1])+total)&255
    return bytes(encoded)


class AesV5:
    INIT=(0xbbb5de72,0xd4638d5b,0x92a5f73b,0x89f8a1c9)
    CON=((2,0,3,1),(1,3,2,0),(3,2,1,0),(2,3,1,0))
    ORDER=(
      (0,9,14,15,4,13,2,7,8,1,6,3,12,5,10,11),
      (0,9,14,11,4,13,2,3,8,1,6,15,12,5,10,7),
      (0,9,14,3,4,13,2,15,8,1,6,11,12,5,10,7),
      (0,9,14,15,4,13,2,3,8,1,6,11,12,5,10,7)
    )
    def __init__(self,key,ts):
        word=ts&3
        initial=self.INIT[word]
        self.sbox=SBOX[word*256:(word+1)*256]
        self.con=self.CON[word]
        self.order=self.ORDER[word]
        expanded=bytearray(struct.pack('<I',initial)*4 + bytes(32))
        for i in range(16): expanded[i]^=key[i]
        rotation=8
        for index in range(4,12):
            at=4*(index-1)
            a,b,c,d=expanded[at:at+4]
            if index&3==0:
                mixed=((initial>>(rotation&24)) ^ self.sbox[b])&255
                b,c,d,a=self.sbox[c],self.sbox[d],self.sbox[a],mixed
            rotation=u32(rotation+2)
            expanded[at+4]=a^expanded[at-12]
            expanded[at+5]=b^expanded[at-11]
            expanded[at+6]=c^expanded[at-10]
            expanded[at+7]=d^expanded[at-9]
        self.keys=[expanded[i*4:i*4+4] for i in range(12)]

    def add_con(self,rows,start):
        for row in range(4):
            for col in range(4): rows[row][col]^=self.keys[start+row][self.con[col]]

    def block(self,data):
        rows=[list(data[r*4:r*4+4]) for r in range(4)]
        self.add_con(rows,0)
        for rnd in (1,2):
            for r in range(4):
                for c in range(4): rows[r][c]=self.sbox[rows[r][c]]
            previous=[r[:] for r in rows]
            for r in range(4): rows[r]=previous[self.con[r]]
            flat=bytes(v for row in rows for v in row)
            for idx in range(16): rows[idx//4][idx%4]=flat[self.order[idx]]
            if rnd==1:
                for r in range(4):
                    prev=rows[r][:]
                    for c in range(4): rows[r][c]=prev[self.con[c]]
                def double(v): return ((v<<1)^(0x1b if v&0x80 else 0))&255
                for c in range(4):
                    s=rows[0][c]^rows[1][c]^rows[2][c]^rows[3][c]
                    first=rows[0][c]
                    rows[0][c]^=s^double(rows[0][c]^rows[1][c])
                    rows[1][c]^=s^double(rows[1][c]^rows[2][c])
                    rows[2][c]^=s^double(rows[2][c]^rows[3][c])
                    rows[3][c]^=s^double(rows[3][c]^first)
            self.add_con(rows,rnd*4)
        for r in range(4):
            for c in range(4): rows[r][c]^=self.keys[4+r][c]
        return bytes(v for row in rows for v in row)

    def encrypt(self,source,iv):
        WIRE=(6,5,3,4,2,7,1,0)
        COMPACT=(1,7,3,6,2,0,4,5)
        if len(source)<248: raise ValueError('V5 AES payload shorter than 248 bytes')
        plain=bytearray(32)
        for i in range(31):
            for j in range(8): plain[i]|=((source[i*8+j]>>WIRE[j])&1)<<COMPACT[j]
        plain[31]=1
        head=bytes(plain[i]^iv[i] for i in range(16))
        first=self.block(head)
        second=self.block(bytes(plain[16+i]^first[i] for i in range(16)))
        bits=first+second
        out=bytearray(source)
        for i in range(31):
            for j in range(8):
                at=i*8+j
                bit=(bits[i]>>COMPACT[j])&1
                out[at]=(out[at]&~(1<<WIRE[j]))|(bit<<WIRE[j])
        return bytes([bits[31]])+bytes(out)


def medusa(query_hash,body_md5,ts,identity,now_ms,nonce,entropy):
    device=proto(p_i(1,1),p_i(2,2),p_s(3,APP_ID),p_s(4,identity['device_id']),
                 p_s(5,nonce),p_s(6,'!noperm!'),p_s(12,'Asia/Shanghai,8'),p_s(13,'zh_CN'),
                 p_s(22,OS_VERSION),p_i(25,now_ms),p_s(30,DEVICE_TYPE),p_s(31,'HONOR'),
                 p_s(32,DEVICE_TYPE),p_s(34,'HONOR'))
    env=proto(p_s(6,'v04.10.03.04-bugfix'),p_s(8,CHANNEL),p_b(12,device),p_s(14,VERSION_NAME))
    f13=hash_f13_v5(query_hash,body_md5,ts)
    flags=bytes(8)
    message=proto(p_b(1,MAGIC),p_i(2,5),p_i(3,entropy['message']),p_s(4,APP_ID),
                  p_s(5,identity['device_id']),p_s(6,str(LICENSE)),p_s(7,VERSION_NAME),
                  p_s(8,'v04.10.03-ml-android'),p_i(9,0x040a0300),p_b(10,flags),
                  p_i(12,ts),p_b(13,f13),p_b(14,query_hash[:6]),
                  p_b(15,proto(p_i(1,1),p_i(2,1),p_i(3,1),p_i(5,1))),
                  p_i(17,ts),p_s(20,'none'),p_i(21,377),p_b(23,env),
                  p_s(24,'{"cmr":16777216,"cmr2":16777216,"un_h":11,"vpn":0,"kd":0,"dyn":"","do":1,"tk":true}'))
    seedhash=sm3(SIGN_KEY+le32(entropy['medusa_nonce'])+SIGN_KEY)
    high=(entropy['medusa_nonce']>>16)&255
    seedword=(((high<<11)|(entropy['medusa_nonce']>>24))^(high>>5)^high)&MASK32
    seed=le32(~seedword)
    transformed=bytearray((flags+xmxor(message,seedhash))[::-1])
    for i in range(len(transformed)): transformed[i]^=seed[3-(i&3)]
    check=0x18000001|((query_hash[0]&63)<<14)|((f13[0]&63)<<8)
    packed=(b'\xca'+le32(entropy['packed'])+le32(check)+bytes(transformed)
            +bytes([(entropy['medusa_nonce']>>16)&255,(entropy['medusa_nonce']>>24)&255]))
    key=hashlib.md5(SIGN_KEY[:16]).digest()
    iv=hashlib.md5(SIGN_KEY[16:]).digest()
    encrypted=AesV5(key,ts).encrypt(packed,iv)
    prefix=bytearray(le32(5)+MAGIC)
    timestamp=le32(ts)
    for i in range(len(prefix)): prefix[i]^=timestamp[i&3]
    return base64.b64encode(bytes(prefix)+bytes([entropy['medusa_nonce']&255,(entropy['medusa_nonce']>>8)&255,0,1])+encrypted).decode()


def sign_v5(base,compressed_body,device,*,now_ms=None,entropy=None):
    """返回 (url, headers)，待发送 body 必须就是 compressed_body 原字节。"""
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    ts=now_ms//1000
    if not 0<=ts<=MASK32: raise ValueError('timestamp out of u32 range')
    cdid=device.get('cdid') or device.get('openudid') or device.get('device_id','')
    identity={'device_id':str(device.get('device_id','')),'install_id':str(device.get('install_id','')),'cdid':cdid}
    if not all(identity.values()): raise ValueError('V5 device identity incomplete')
    bodyhash=hashlib.md5(compressed_body).digest()
    for offset in range(1024):
        ticket=now_ms+offset
        values=(('aid',APP_ID),('app_name','novelread'),('version_code',VERSION_CODE),
                ('version_name',VERSION_NAME),('channel',CHANNEL),('device_platform','android'),
                ('device_type',DEVICE_TYPE),('os_version',OS_VERSION),('update_version_code',VERSION_CODE),
                ('iid',identity['install_id']),('cdid',identity['cdid']),('device_id',identity['device_id']))
        query='&'.join(f'{quote(k,safe="-_.*~")}={quote(v,safe="-_.*~")}' for k,v in values)+f'&_rticket={ticket}'
        queryhash=sm3(query.encode())
        if branch_iv(queryhash,bodyhash,ts)&15!=12: continue
        entropy = entropy or {k:secrets.randbits(32) for k in ('helios','medusa_nonce','packed','message')}
        meds=medusa(queryhash,bodyhash,ts,identity,_PROCESS_STARTED,_PROCESS_NONCE,entropy)
        headers={'x-khronos':str(ts),'x-helios':ladon(ts,LICENSE,int(APP_ID),le32(entropy['helios'])),
                 'x-medusa':meds,'User-Agent':USER_AGENT,'Accept':'application/json',
                 'Content-Type':'application/json; charset=utf-8','Content-Encoding':'gzip',
                 'x-ss-stub':bodyhash.hex().upper()}
        return base+'?'+query,headers
    raise ValueError('No branch-12 v5 ticket')
