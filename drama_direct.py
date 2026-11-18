#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红果短剧直连上游的 Python 客户端 (GPL-3.0)，无需什锦铺 App/Rust/Frida。
移植自 https://github.com/ch6vip/novel-sound 的注册、搜索、目录、详情和 V5 取流。
按原协议处理上游的内容响应；错误回包保留真实失败状态。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import random
import secrets
import string
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

import requests
from drama_sign import aes_cbc_enc, aes_cbc_dec, pad16, sign_headers
from drama_v5 import sign_v5

TT_FIXED=base64.b64decode('TdTC5rgxYgkOUrPHpnM7pByyRiuCmrWKGWs521cXdST0m69/COjWjSanLjfBqVovHwWlGJKu8pSXMrYqOKrdWA==')
SPADE_CONSTANTS=bytes([77,212,194,230,184,49,98,9,14,82,179,199,166,115,59,164,28,178,70,43,130,154,181,138,25,107,57,219,87,23,117,36,244,155,175,127,8,232,214,141,38,167,46,55,193,169,90,47,31,5,165,24,146,174,242,148,151,50,182,42,56,170,221,88])
DEFAULT_UA='com.dragon.read/66732 (Linux; U; Android 10; zh_CN; Pixel 4 XL; Build/QD1A.190821.007;tt-ok/3.12.13.4-tiktok)'
DRAGON_UA='com.dragon.read/65132 (Linux; U; Android 10; zh_CN; MI 12; Build/MMB29M; Cronet/TTNetVersion:9ac8d95c 2024-11-25 QuicVersion:3f326df4 2024-11-14)'
VERSION='65132'
VIDEO_DETAIL_UA='com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 QuicVersion:c67e9834 2025-09-08)'
VIDEO_DETAIL_URL=('https://api5-normal-sinfonlineb.fqnovel.com/novel/player/video_detail/v1/?iid={install_id}'
    '&device_id={device_id}&ac=wifi&channel=update_64&aid=8662&app_name=novelread&version_code=71332'
    '&version_name=7.1.3.32&device_platform=android&os=android&ssmix=a&device_type=25053RT47C'
    '&device_brand=Redmi&language=zh&os_api=36&os_version=16&manifest_version_code=71332'
    '&resolution=1280*2772&dpi=520&update_version_code=71332&host_abi=arm64-v8a&dragon_device_type=phone'
    '&pv_player=71332&compliance_status=0&need_personal_recommend=1&player_so_load=1&is_android_pad_screen=0')
DIRECTORY_SUFFIX=('&book_info_md5=&need_version=true&device_id={device_id}&ac=wifi&channel=xiaomi_1967_64'
    '&aid=1967&app_name=novelapp&version_code=65132&version_name=6.5.1.32&device_platform=android'
    '&os=android&ssmix=a&device_type=FRD-AL10&device_brand=honor&language=zh&os_api=28'
    '&os_version=9&manifest_version_code=65132&resolution=1080*1920&dpi=480&update_version_code=65132'
    '&pv_player=65132&=&need_personal_recommend=1&player_so_load=1&is_android_pad_screen=0'
    '&host_abi=arm64-v8a&dragon_device_type=phone&rom_version=FRD-AL10+8.0.0.556%28C00%29&compliance_status=0')
SEARCH_SUFFIX=('&tab_name=store&pad_column_cover=0&is_first_enter_search=false&iid={install_id}'
    '&device_id={device_id}&ac=wifi&channel=xiaomi_1967_64&aid=1967&app_name=novelapp'
    '&version_code=65132&version_name=6.5.1.32&device_platform=android&os=android&ssmix=a'
    '&device_type=FRD-AL10&device_brand=honor&language=zh&os_api=28&os_version=9'
    '&manifest_version_code=65132&resolution=1080%2A1920&dpi=480&update_version_code=65132'
    '&pv_player=65132&gender=2&need_personal_recommend=1&player_so_load=1&is_android_pad_screen=0'
    '&host_abi=arm64-v8a&dragon_device_type=phone&rom_version=FRD-AL10%2B8.0.0.556%28C00%29&compliance_status=0')


class DirectError(RuntimeError):
    pass


def tt_encrypt(data:bytes, randomness:bytes|None=None)->bytes:
    random32 = randomness if randomness is not None else secrets.token_bytes(32)
    if len(random32)!=32: raise ValueError('tt_random must be 32 bytes')
    h=hashlib.sha512(hashlib.sha512(random32).digest()+TT_FIXED).digest()
    payload=hashlib.sha512(data).digest()+data
    return b'\x74\x63\x05\x10\x00\x00'+random32+aes_cbc_enc(pad16(payload),h[:16],h[16:32])


def json_body(obj:dict)->bytes:
    return json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()


def random_alnum(n):
    return ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(n))


def reg_params(info):
    now=int(time.time()); ms=int(time.time()*1000)
    return dict(tt_data='a',os_api=info['os_api'],device_type=info['device_type'],ssmix='a',
        manifest_version_code=info['version_code'],dpi=info['dpi'],version_name=info['version_name'],
        ts=str(now),cpu_support64='true',app_type='normal',appTheme='light',ac='wifi',host_abi='arm64-v8a',
        update_version_code=info['version_code'],channel='xiaomi_1967_64',_rticket=str(ms),
        device_platform='android',version_code=info['version_code'],cdid=info['cdid'],os='android',
        is_android_pad='0',openudid=info['openudid'],package='com.dragon.read',resolution=info['resolution'],
        os_version=info['os_version'],language='zh',device_brand='Xiaomi',need_personal_recommend='1',
        aid='1967',minor_status='0',app_name='novel',mcc_mnc='46007')


def register_device(*, mock_origin: str|None=None, timeout=20)->dict:
    """执行上游 device_register，并读取服务端返回的设备标识。"""
    dev={'device_type':'MI 12','os_api':'29','os_version':'10','resolution':'1440*2392','dpi':'560',
         'cdid':str(uuid.uuid4()),'uuid':str(uuid.uuid4()),'clientudid':str(uuid.uuid4()),
         'openudid':secrets.token_hex(8),'version_code':VERSION,'version_name':'6.5.1.32'}
    ua=DRAGON_UA
    header={
       'display_name':'番茄小说','update_version_code':VERSION,'manifest_version_code':VERSION,
       'aid':1967,'channel':'xiaomi_1967_64','package':'com.dragon.read','app_version':dev['version_name'],
       'version_code':VERSION,'sdk_version':'v04.04.05-ov-android','sdk_target_version':29,
       'os':'Android','os_version':'10','os_api':'29','device_model':'MI 12','device_brand':'Xiaomi',
       'device_manufacturer':'Google','device_category':'phone','cpu_abi':'arm64-v8a','release_build':str(uuid.uuid4()),
       'density_dpi':'560','display_density':'mdpi','resolution':'1440x2392','language':'zh',
       'mac':':'.join(secrets.token_hex(1).upper() for _ in range(6)), 'timezone':8,
       'access':'wifi','not_request_sender':0,'carrier':'CHINA MOBILE','mcc_mnc':'46007',
       'rom':'EMUI-'+random_alnum(13),'rom_version':random_alnum(2),
       'sig_hash':hashlib.md5(str(uuid.uuid4()).encode()).hexdigest(),'openudid':dev['openudid'],
       'clientudid':dev['clientudid'],'sim_serial_number':[],'region':'CN','tz_name':'Asia/Shanghai',
       'tz_offset':28800,'sim_region':'cn'
    }
    body={'magic_tag':'ss_app_log','header':header,'_gen_time':int(time.time()*1000)}
    encoded=tt_encrypt(gzip.compress(json_body(body)))
    url='https://log.snssdk.com/service/2/device_register/?'+urlencode(sorted(reg_params(dev).items()))
    if mock_origin:
        u=urlsplit(url);url=mock_origin.rstrip('/')+u.path+'?'+u.query
    try:
        response=requests.post(url,data=encoded,headers={'Content-Type':'application/octet-stream;tt-data=a',
              'Accept-Encoding':'gzip','User-Agent':ua},timeout=timeout)
        response.raise_for_status()
        j=response.json()
    except (requests.RequestException,ValueError) as e:
        raise DirectError(f'设备注册失败: {e}') from e
    device_id=str(j.get('device_id_str') or j.get('device_id') or '')
    install_id=str(j.get('install_id_str') or j.get('install_id') or '')
    if not device_id.strip() or not install_id.strip():
        raise DirectError(f'设备注册未返回有效ID，响应状态：{str(j.get("message") or j.get("error") or j.get("code"))[:200]}')
    return {'device_id':device_id,'install_id':install_id,'cdid':dev['cdid'],
            'openudid':dev['openudid'],'status':'active','registered_version_code':VERSION,
            'created_time':time.strftime('%Y-%m-%d %H:%M:%S')}


def generate_local_device() -> dict:
    """Create a local device identity to try before server registration.

    This is an active *local candidate*, not a claim of server-side
    registration. The first successful upstream request confirms usability;
    authentication rejection triggers real device registration.
    """
    return {
        'device_id': str(secrets.randbelow(9 * 10**17) + 10**17),
        'install_id': str(secrets.randbelow(9 * 10**17) + 10**17),
        'cdid': str(uuid.uuid4()),
        'openudid': secrets.token_hex(8),
        'status': 'active',
        'origin': 'local_generated',
        'server_verified': False,
        'registered_version_code': VERSION,
        'created_time': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


class DevicePool:
    """可导入什锦铺原始 JSON 池，自动注册并轮询，线程安全落盘。"""
    def __init__(self,path:str,desired=2,mock_origin=None):
        self.path=Path(path)
        self.desired=desired
        self.mock_origin=mock_origin
        self._lock=threading.RLock()
        self._seq=0
        self._devices=[]
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            value=json.loads(self.path.read_text('utf-8'))
            raw=value.get('android',[]) if isinstance(value,dict) else value
            if not isinstance(raw,list): raise DirectError('设备池 JSON 非数组')
            self._devices=[dict(d) for d in raw if isinstance(d,dict)
                  and str(d.get('device_id','')).strip() and str(d.get('install_id','')).strip()
                  and d.get('status','active')=='active']

    def _save(self):
        output={'android':self._devices,'last_update':time.strftime('%Y-%m-%d %H:%M:%S')}
        temp=self.path.with_name(self.path.name+'.tmp')
        with temp.open('w',encoding='utf-8') as f:
            json.dump(output,f,ensure_ascii=False,indent=2)
        try: os.chmod(temp,0o600)
        except OSError: pass
        os.replace(temp,self.path)

    def ensure(self, count=None):
        """Explicit prefill: try registration; use local IDs if unavailable."""
        n=self.desired if count is None else count
        if not 1<=n<=20: raise DirectError('设备池数量需在 1–20')
        with self._lock:
            registration_failed=False
            while len(self._devices)<n:
                if not registration_failed:
                    try:
                        device=register_device(mock_origin=self.mock_origin)
                        device.setdefault('origin','server_registered')
                    except (DirectError, requests.RequestException):
                        registration_failed=True
                if registration_failed or any(x['device_id']==device['device_id'] for x in self._devices):
                    device=generate_local_device()
                while any(x['device_id']==device['device_id'] for x in self._devices):
                    device=generate_local_device()
                device.setdefault('server_verified',False)
                self._devices.append(device)
                self._save()
            return len(self._devices)

    def get(self):
        with self._lock:
            if not self._devices:
                device=generate_local_device()
                self._devices.append(device)
                self._save()
            device=self._devices[self._seq%len(self._devices)]
            self._seq+=1
            device['use_count']=int(device.get('use_count',0))+1
            return dict(device)

    def mark_verified(self, device_id):
        with self._lock:
            for device in self._devices:
                if device['device_id']==device_id and not device.get('server_verified'):
                    device['server_verified']=True
                    self._save()
                    break

    def replace_rejected(self, device_id):
        """Try other cached identities first; register only if none remain.

        Serialize replacement so simultaneous rejected requests cannot race
        to register duplicate replacement devices.
        """
        with self._lock:
            self._devices=[d for d in self._devices if d['device_id']!=device_id]
            self._save()
            if self._devices:
                return dict(self._devices[0])
            try:
                device=register_device(mock_origin=self.mock_origin)
                device.setdefault('origin','server_registered')
            except (DirectError, requests.RequestException):
                # When even registration is unavailable, keep trying a fresh
                # local identity rather than inventing server verification.
                device=generate_local_device()
            device.setdefault('server_verified',False)
            self._devices.append(device)
            self._save()
            return dict(device)

    def status(self):
        with self._lock:
            return [{'device_id':d['device_id'], 'status':d.get('status','active'),
                     'origin':d.get('origin','existing'), 
                     'server_verified':bool(d.get('server_verified',False)),
                     'use_count':d.get('use_count',0)} for d in self._devices]

    def retire(self,device_id):
        with self._lock:
            self._devices=[d for d in self._devices if d['device_id']!=device_id]
            self._save()


def b64decode(text):
    return base64.b64decode(text.strip()+'='*((-len(text.strip()))%4))


def decrypt_spade_url(text,seed):
    if text.startswith(('http://','https://')): return text
    try: data=b64decode(text)
    except (ValueError,base64.binascii.Error): return ''
    if data.startswith(b'http'): return data.decode('utf-8','replace')
    if len(data)<5 or data[0]!=0xa8 or data[2]!=1 or data[3]!=0 or not seed:return ''
    ct=data[4:4+((len(data)-4)//16)*16]
    if not ct:return ''
    h=hashlib.sha512(hashlib.sha512(seed).digest()+SPADE_CONSTANTS).digest()
    plain=aes_cbc_dec(ct,h[:16],h[16:32])
    padding=plain[-1]
    if 1<=padding<=16 and plain[-padding:]==bytes([padding])*padding:
        plain=plain[:-padding]
    return plain.rstrip(b'\0').decode('utf-8','replace')


def derive_spade_key(value):
    try: raw=b64decode(value)
    except (ValueError,base64.binascii.Error): return ''
    if len(raw)<3:return ''
    n=len(raw)-(raw[0]^raw[1]^raw[2])+47
    if n<33 or n+1>len(raw):return ''
    src=raw[1:1+n]
    prev_a,prev_b=0x55,0xF6
    buf=bytearray(n)
    for i,cur in enumerate(src):
        if i&1:
            keep=cur
        else:
            keep=prev_a
            prev_a,prev_b=prev_b,cur
        buf[i]=((prev_a^cur)-i.bit_count()-0x15)&255
        prev_a=keep
    try:
        k=bytes.fromhex(bytes(buf[1:33]).decode())
        return k.hex() if len(k)==16 else ''
    except (ValueError,UnicodeDecodeError): return ''


def _int(text):
    try:return int(str(text))
    except (TypeError,ValueError):return 0


def _codec_ok(info):
    meta=info.get('video_meta') if isinstance(info.get('video_meta'),dict) else {}
    codec=' '.join(str(x or '') for x in (info.get('codec_type'),meta.get('codec_type'),info.get('gear_des_key'))).lower()
    return 'bytevc2' not in codec


def _metric(info,key):
    meta=info.get('video_meta') if isinstance(info.get('video_meta'),dict) else {}
    return _int(meta.get(key) or info.get(key))


def parse_v5_sources(payload,vid, *, include_bytevc2=False, fetch_fallback=None):
    """解析 V5 视频模型所有可播放档位，保留来源质量和内容密钥。"""
    data=payload.get('data',{})
    if not isinstance(data,dict):raise DirectError('V5响应没有data对象')
    candidates=[]
    for group in (data.get('1004'), data):
        if isinstance(group,dict):
            entry=group.get(vid)
            if isinstance(entry,dict):candidates.append(entry)
    if not candidates:
        for group in data.values():
            if isinstance(group,dict) and isinstance(group.get(vid),dict):
                candidates.append(group[vid])
    if not candidates:raise DirectError(f'V5没有视频 {vid} 的模型; code={payload.get("code")}')
    output=[]
    seen=set()
    models=[]
    for entry in candidates:
        model=entry.get('video_model') or entry
        if isinstance(model,str):
            try:model=json.loads(model)
            except ValueError:continue
        if not isinstance(model,dict):continue
        models.append(model)
        fallback=model.get('fallback_api')
        if isinstance(fallback,dict):fallback=fallback.get('fallback_api','')
        if isinstance(fallback,str) and fallback.startswith('{'):
            try:fallback=json.loads(fallback).get('fallback_api','')
            except ValueError:pass
        if fetch_fallback and isinstance(fallback,str) and fallback.startswith('https://'):
            try:
                remote=fetch_fallback(fallback)
                fallback_data=((remote.get('video_info') or {}).get('data') or {})
                if isinstance(fallback_data,dict) and fallback_data.get('video_list'):
                    models.append(fallback_data)
            except (requests.RequestException,DirectError,ValueError,TypeError):
                # 回退源只是扩展清晰度，不覆盖主源能拿到的有效档位。
                pass
    for model in models:
        seed=b''
        if model.get('key_seed'):
            try:seed=b64decode(model['key_seed'])
            except (ValueError,base64.binascii.Error):pass
        video_list=model.get('video_list',[])
        infos=video_list.values() if isinstance(video_list,dict) else video_list if isinstance(video_list,list) else []
        for info in infos:
            if not isinstance(info,dict):continue
            if not include_bytevc2 and not _codec_ok(info):continue
            url=''
            for name in ('main_url','backup_url','backup_url_1','play_url'):
                text=info.get(name)
                if not isinstance(text,str) or not text:continue
                url=decrypt_spade_url(text,seed)
                if url.startswith(('https://','http://')):break
            if not url.startswith(('https://','http://')) or url in seen:continue
            seen.add(url)
            w,h=_metric(info,'vwidth'),_metric(info,'vheight')
            quality=min(w,h) if w>0 and h>0 else 0
            enc=info.get('encrypt_info') if isinstance(info.get('encrypt_info'),dict) else {}
            key=derive_spade_key(str(info.get('spade_a') or enc.get('spade_a') or ''))
            codec=str((info.get('video_meta') or {}).get('codec_type') if isinstance(info.get('video_meta'),dict) else info.get('codec_type') or '')
            output.append({'name':f'{quality}P' if quality else '未知','url':url,'key_hex':key,
                           'quality_height':quality,'width':w,'height':h,'codec':codec})
    output.sort(key=lambda x:(x['quality_height'],x['width'],x['height']),reverse=True)
    return {'video_url':output[0]['url'] if output else '',
            'key_hex':output[0]['key_hex'] if output else '', 'variants':output}


class DirectClient:
    """不访问本地 Rust HTTP；每个请求选择设备、实时生成签名，直连上游。"""
    def __init__(self,pool:DevicePool,*,timeout=(10,40),mock_origin=None):
        self.pool=pool
        self.timeout=timeout
        self.mock_origin=mock_origin

    def _url(self,url):
        if not self.mock_origin:return url
        u=urlsplit(url)
        return self.mock_origin.rstrip('/')+u.path+('?' +u.query if u.query else '')

    def request(self,url,*,method='GET',body=None,headers=None,sign=True,retries=2):
        last=None
        for attempt in range(retries):
            dev=self.pool.get()
            target=url
            for k,v in [('device_id',dev['device_id']),('install_id',dev['install_id']),
                        ('secret_key',dev.get('secret_key','')),('cdid',dev.get('cdid','')),
                        ('openudid',dev.get('openudid') or dev['device_id'])]:
                target=target.replace('{'+k+'}',str(v))
            query=urlsplit(target).query
            hdr={'User-Agent':DEFAULT_UA}
            hdr.update(headers or {})
            if sign:hdr.update(sign_headers(query,body))
            try:
                r=requests.request(method,self._url(target),data=body,headers=hdr,timeout=self.timeout)
                if r.status_code in (401,403):
                    self.pool.replace_rejected(dev['device_id'])
                    last=DirectError(f'上游 HTTP {r.status_code} 拒绝设备，已尝试池内替换或注册')
                    continue
                r.raise_for_status()
                j=r.json()
                if not isinstance(j,dict):raise DirectError('接口返回非JSON对象')
                if str(j.get('code')) in ('401','403'):
                    self.pool.replace_rejected(dev['device_id'])
                    last=DirectError('上游业务响应拒绝该设备，已尝试替换或注册')
                    continue
                if j.get('success') is False:raise DirectError(str(j.get('message') or j.get('error') or '上游拒绝'))
                if j.get('code') not in (None,0,'0',200,'200'):
                    raise DirectError(str(j.get('message') or j.get('error') or '业务错误'+str(j.get('code'))))
                self.pool.mark_verified(dev['device_id'])
                return j
            except (requests.RequestException,ValueError,DirectError) as exc:
                last=exc
                if attempt+1<retries:time.sleep(0.4*(attempt+1))
        raise DirectError(f'上游请求失败：{last}')

    def search(self,keyword:str,offset=0,count=10,tab_type=11):
        esc=quote_plus
        url=('https://api5-normal-sinfonlineb.fqnovel.com/reading/bookapi/search/tab/v?'
             + f'bookshelf_search_plan=4&offset={offset}&user_is_login=1&bookstore_tab=2&passback={offset}'
             + f'&query={esc(keyword)}&count={count}&search_source=1&clicked_content=search_history'
             + f'&use_lynx=false&use_correct=true&tab_type={quote_plus(str(tab_type))}'+SEARCH_SUFFIX)
        return self.request(url,headers={'User-Agent':DRAGON_UA})

    def video_series(self, series_id):
        """Get authoritative short-drama episode numbers and video IDs.

        The generic reading directory is NOT an episode-number source: its
        chapter titles may start at 110 even for a 109-episode series.
        """
        series_id = str(series_id)
        query = VIDEO_DETAIL_URL.partition('?')[2]
        base = 'https://api5-normal-sinfonlineb.fqnovel.com'
        biz = {
            'detail_page_version': 0,
            'disable_digg_stat': False,
            'disable_video_relate_book': False,
            'image_shrink_datas_str': 'W3siaW1hZ2VfdHlwZSI6MywiaW1hZ2Vfd2lkdGgiOjkwMCwic2hyaW5rX3R5cGUiOjN9LHsiaW1hZ2VfdHlwZSI6NCwiaW1hZ2Vfd2lkdGgiOjcyLCJzaHJpbmtfdHlwZSI6NH1d\n',
            'need_all_video_definition': False,
            'need_mp4_align': False,
            'screen_width_px': '900',
            'source': 7,
            'use_os_player': False,
            'use_server_dns': False,
        }
        headers = {
            'User-Agent': VIDEO_DETAIL_UA,
            'Accept': 'application/json; charset=utf-8,application/x-protobuf',
            'Content-Type': 'application/json; charset=utf-8',
            'x-xs-from-web': '0',
            'sdk-version': '2',
        }
        attempts = (
            ('/novel/player/multi_video_detail/preload/v1',
             {'biz_param': biz, 'dr_scene': 'preload', 'series_id': series_id}),
            ('/novel/player/multi_video_detail/v1/',
             {'biz_param': biz, 'series_id': series_id}),
        )
        errors = []
        for route, payload in attempts:
            try:
                result = self.request(base + route + '?' + query, method='POST',
                                      body=json_body(payload), headers=headers)
                data = result.get('data')
                item = data.get(series_id) if isinstance(data, dict) else None
                video = item.get('video_data') if isinstance(item, dict) else None
                video_list = video.get('video_list') if isinstance(video, dict) else None
                if not video_list:
                    raise DirectError('短剧详情缺少 video_list（不可使用小说目录猜测集号）')
                return result
            except (requests.RequestException, ValueError, TypeError, DirectError) as exc:
                errors.append(f'{route}: {exc}')
        raise DirectError('短剧专用剧集目录获取失败：' + '; '.join(errors))

    def directory(self,series_id):
        esc=quote_plus
        url=('https://api5-normal-sinfonlineb.fqnovel.com/reading/bookapi/directory/all_items/v?'
             +f'book_type=0&item_data_list_md5=&catalog_data_md5=&book_id={esc(series_id)}'+DIRECTORY_SUFFIX)
        return self.request(url,headers={'User-Agent':'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36'})

    def info(self,series_id):
        body=json_body({'series_id':str(series_id),'biz_param':{'device_level':3,
            'need_all_video_definition':True,'need_mp4_align':False,'source':5,
            'use_os_player':False,'video_id_type':1}})
        return self.request(VIDEO_DETAIL_URL,method='POST',body=body,
                  headers={'User-Agent':VIDEO_DETAIL_UA,'Accept':'application/json; charset=utf-8,application/x-protobuf',
                  'Content-Type':'application/json','x-xs-from-web':'0','sdk-version':'2'})

    def source(self,video_id,*,include_bytevc2=False):
        payload={'biz_param':{'detail_page_version':0,'device_level':1,
            'disable_digg_stat':False,'disable_video_relate_book':False,
            'need_all_video_definition':True,'need_mp4_align':False,'use_os_player':False,
            'use_server_dns':False,'video_platform':1024},'mixed_video_id_map':{'1004':[str(video_id)]},
            'dr_scene':'player'}
        body=gzip.compress(json_body(payload))
        base='https://api5-normal-sinfonlinea.fqnovel.com/novel/player/multi_video_model/player/v1'
        last=None
        for i in range(2):
            dev=self.pool.get()
            url,headers=sign_v5(base,body,dev)
            try:
                response=requests.post(self._url(url),headers=headers,data=body,timeout=self.timeout)
                if response.status_code in (401,403):
                    self.pool.replace_rejected(dev['device_id'])
                    raise DirectError('V5设备鉴权失败，已尝试替换或注册')
                response.raise_for_status()
                data=response.json()
                if not isinstance(data,dict):raise DirectError('V5没有返回JSON对象')
                if str(data.get('code')) in ('401','403'):
                    self.pool.replace_rejected(dev['device_id'])
                    raise DirectError('V5业务响应拒绝设备，已尝试替换或注册')
                if data.get('success') is False or data.get('code') not in (None,0,'0',200,'200'):
                    raise DirectError(str(data.get('message') or data.get('error') or data.get('code')))
                self.pool.mark_verified(dev['device_id'])
                def fetch_fallback(url):
                    # 官方 fallback_api 为不带请求签名的 https JSON 地址。
                    response=requests.get(self._url(url),headers={'User-Agent':
                         'com.xs.fm/632 (Linux; U; Android 15; zh_CN; 23049RAD8C; Build/AQ3A.250226.002)'},
                         timeout=self.timeout)
                    response.raise_for_status()
                    result=response.json()
                    if not isinstance(result,dict):raise DirectError('fallback 非JSON')
                    return result
                result=parse_v5_sources(data,str(video_id),include_bytevc2=include_bytevc2,
                                        fetch_fallback=fetch_fallback)
                if not result['variants']:
                    raise DirectError('V5未返回兼容的清晰度流，可能需要官方的 fallback_api 支路')
                return result
            except (requests.RequestException,ValueError,DirectError) as exc:
                last=exc
                if i==0:time.sleep(.5)
        raise DirectError(f'视频取流失败: {last}')
