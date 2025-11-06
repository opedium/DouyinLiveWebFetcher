#!/usr/bin/python
# coding:utf-8

# @FileName:    liveMan.py
# @Time:        2024/1/2 21:51
# @Author:      bubu
# @Project:     douyinLiveWebFetcher

import codecs
import gzip
import hashlib
import random
import re
import string
import subprocess
import threading
import time
import execjs
import urllib.parse
from contextlib import contextmanager
from unittest.mock import patch

import requests
import websocket
from py_mini_racer import MiniRacer

from ac_signature import get__ac_signature
from protobuf.douyin import *

from urllib3.util.url import parse_url

from datetime import datetime
import csv
import os
import yaml


def parse_chinese_number(text): #万转成数字
    try:
        if isinstance(text, str):
            if '万' in text:
                num = float(text.replace('万', '')) * 10000
            else:
                num = float(text)
            return int(num)
        return int(text)
    except Exception:
        return 0


def execute_js(js_file: str):
    """
    执行 JavaScript 文件
    :param js_file: JavaScript 文件路径
    :return: 执行结果
    """
    with open(js_file, 'r', encoding='utf-8') as file:
        js_code = file.read()
    
    ctx = execjs.compile(js_code)
    return ctx

from collections import defaultdict

diamond_totals = defaultdict(lambda: {"name": "", "diamonds": 0})


@contextmanager
def patched_popen_encoding(encoding='utf-8'):
    original_popen_init = subprocess.Popen.__init__
    
    def new_popen_init(self, *args, **kwargs):
        kwargs['encoding'] = encoding
        original_popen_init(self, *args, **kwargs)
    
    with patch.object(subprocess.Popen, '__init__', new_popen_init):
        yield


def generateSignature(wss, script_file='sign.js'):
    """
    出现gbk编码问题则修改 python模块subprocess.py的源码中Popen类的__init__函数参数encoding值为 "utf-8"
    """
    params = ("live_id,aid,version_code,webcast_sdk_version,"
              "room_id,sub_room_id,sub_channel_id,did_rule,"
              "user_unique_id,device_platform,device_type,ac,"
              "identity").split(',')
    wss_params = urllib.parse.urlparse(wss).query.split('&')
    wss_maps = {i.split('=')[0]: i.split("=")[-1] for i in wss_params}
    tpl_params = [f"{i}={wss_maps.get(i, '')}" for i in params]
    param = ','.join(tpl_params)
    md5 = hashlib.md5()
    md5.update(param.encode())
    md5_param = md5.hexdigest()
    
    with codecs.open(script_file, 'r', encoding='utf8') as f:
        script = f.read()
    
    ctx = MiniRacer()
    ctx.eval(script)
    
    try:
        signature = ctx.call("get_sign", md5_param)
        return signature
    except Exception as e:
        print(e)
    
    # 以下代码对应js脚本为sign_v0.js
    # context = execjs.compile(script)
    # with patched_popen_encoding(encoding='utf-8'):
    #     ret = context.call('getSign', {'X-MS-STUB': md5_param})
    # return ret.get('X-Bogus')


def generateMsToken(length=182):
    """
    产生请求头部cookie中的msToken字段，其实为随机的107位字符
    :param length:字符位数
    :return:msToken
    """
    random_str = ''
    base_str = string.ascii_letters + string.digits + '-_'
    _len = len(base_str) - 1
    for _ in range(length):
        random_str += base_str[random.randint(0, _len)]
    return random_str


class DouyinLiveWebFetcher:

    def load_message_handlers(self, config_path="message_handlers.yml"):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.handler_config = yaml.safe_load(f)
            return {
                msg_type: getattr(self, cfg["handler"])
                for msg_type, cfg in self.handler_config.items()
                if isinstance(cfg, dict) and cfg.get("enabled", False)
            }
        except Exception as e:
            print(f"【配置加载失败】{e}")
            self.handler_config = {}
            return {}

    def __init__(self, live_id, abogus_file='a_bogus.js', config_path="message_handlers.yml"):
        self.abogus_file = abogus_file
        self.__ttwid = None
        self.__room_id = None
        self.session = requests.Session()
        self.live_id = live_id
        self.host = "https://www.douyin.com/"
        self.live_url = "https://live.douyin.com/"
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        self.headers = {
            'User-Agent': self.user_agent
        }
        # 加载配置
        with open(config_path, "r", encoding="utf-8") as f:
            self.handler_config = yaml.safe_load(f)
        self.total_diamonds = 0

        # 运行时设置
        self.heartbeat_interval = self.handler_config.get("heartbeat_interval", 5)
        self.retry_on_failure = self.handler_config.get("retry_on_failure", True)
        self.max_retries = self.handler_config.get("max_retries", 3)
        self.retry_delay_seconds = self.handler_config.get("retry_delay_seconds", 10)

        self.logging_cfg = self.handler_config.get("logging", {})
        self.log_folder = self.logging_cfg.get("folder", "logs")
        self.log_format = self.logging_cfg.get("format", "csv")
        self.rotate_daily = self.logging_cfg.get("rotate_daily", True)
        self.include_timestamp = self.logging_cfg.get("include_timestamp", True)

        os.makedirs(self.log_folder, exist_ok=True)


            
    def start(self):
        self._connectWebSocket()
    
    def stop(self):
        self.ws.close()
    
    @property
    def ttwid(self):
        """
        产生请求头部cookie中的ttwid字段，访问抖音网页版直播间首页可以获取到响应cookie中的ttwid
        :return: ttwid
        """
        if self.__ttwid:
            return self.__ttwid
        headers = {
            "User-Agent": self.user_agent,
        }
        try:
            response = self.session.get(self.live_url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            print("【X】Request the live url error: ", err)
        else:
            self.__ttwid = response.cookies.get('ttwid')
            return self.__ttwid
    
    @property
    def room_id(self):
        """
        根据直播间的地址获取到真正的直播间roomId，有时会有错误，可以重试请求解决
        :return: room_id
        """
        if self.__room_id:
            return self.__room_id
        url = self.live_url + self.live_id
        headers = {
            "User-Agent": self.user_agent,
            "cookie": f"ttwid={self.ttwid}&msToken={generateMsToken()}; __ac_nonce=0123407cc00a9e438deb4",
        }
        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            print("【X】Request the live room url error: ", err)
        else:
            match = re.search(r'roomId\\":\\"(\d+)\\"', response.text)
            if match is None or len(match.groups()) < 1:
                print("【X】No match found for roomId")
            
            self.__room_id = match.group(1)
            
            return self.__room_id

    
    def get_ac_nonce(self):
        """
        获取 __ac_nonce
        """
        resp_cookies = self.session.get(self.host, headers=self.headers).cookies
        return resp_cookies.get("__ac_nonce")
    
    def get_ac_signature(self, __ac_nonce: str = None) -> str:
        """
        获取 __ac_signature
        """
        __ac_signature = get__ac_signature(self.host[8:], __ac_nonce, self.user_agent)
        self.session.cookies.set("__ac_signature", __ac_signature)
        return __ac_signature
    
    def get_a_bogus(self, url_params: dict):
        """
        获取 a_bogus
        """
        url = urllib.parse.urlencode(url_params)
        ctx = execute_js(self.abogus_file)
        _a_bogus = ctx.call("get_ab", url, self.user_agent)
        return _a_bogus
    
    def get_room_status(self):
        """
        获取直播间开播状态:
        room_status: 2 直播已结束
        room_status: 0 直播进行中
        """
        msToken = generateMsToken()
        nonce = self.get_ac_nonce()
        signature = self.get_ac_signature(nonce)
        url = ('https://live.douyin.com/webcast/room/web/enter/?aid=6383'
               '&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&enter_from=page_refresh'
               '&cookie_enabled=true&screen_width=5120&screen_height=1440&browser_language=zh-CN&browser_platform=Win32'
               '&browser_name=Edge&browser_version=140.0.0.0'
               f'&web_rid={self.live_id}'
               f'&room_id_str={self.room_id}'
               '&enter_source=&is_need_double_stream=false&insert_task_id=&live_reason=&msToken=' + msToken)
        query = parse_url(url).query
        params = {i[0]: i[1] for i in [j.split('=') for j in query.split('&')]}
        a_bogus = self.get_a_bogus(params)  # 计算a_bogus,成功率不是100%，出现失败时重试即可
        url += f"&a_bogus={a_bogus}"
        headers = self.headers.copy()
        headers.update({
            'Referer': f'https://live.douyin.com/{self.live_id}',
            'Cookie': f'ttwid={self.ttwid};__ac_nonce={nonce}; __ac_signature={signature}',
        })
        resp = self.session.get(url, headers=headers)
        data = resp.json().get('data')
        if data:
            room_status = data.get('room_status')
            user = data.get('user')
            user_id = user.get('id_str')
            nickname = user.get('nickname')

            self.streamer_name = nickname  

            print(f"【{nickname}】[{user_id}]直播间：{['正在直播', '已结束'][bool(room_status)]}.")

    def _connectWebSocket(self):
        """
        连接抖音直播间websocket服务器，请求直播间数据
        """
        attempt = 0
        while attempt < self.max_retries:
            try:
                wss = ("wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
                    "&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
                    "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
                    "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
                    "&browser_name=Mozilla"
                    "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
                    "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
                    "&browser_online=true&tz_name=Asia/Shanghai"
                    "&cursor=d-1_u-1_fh-7392091211001140287_t-1721106114633_r-1"
                    f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:7319483754668557238"
                    f"|first_req_ms:1721106114541|fetch_time:1721106114633|seq:1|wss_info:0-1721106114633-0-0|"
                    f"wrds_v:7392094459690748497"
                    f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
                    f"&user_unique_id=7319483754668557238&im_path=/webcast/im/fetch/&identity=audience"
                    f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0")

                signature = generateSignature(wss)
                wss += f"&signature={signature}"

                headers = {
                    "cookie": f"ttwid={self.ttwid}",
                    'user-agent': self.user_agent,
                }

                self.ws = websocket.WebSocketApp(
                    wss,
                    header=headers,
                    on_open=self._wsOnOpen,
                    on_message=self._wsOnMessage,
                    on_error=self._wsOnError,
                    on_close=self._wsOnClose
                )

                print(f"【连接尝试】第 {attempt + 1} 次连接 WebSocket...")
                self.ws.run_forever()
                break  # success, exit loop

            except Exception as e:
                print(f"【连接失败】{e}")
                attempt += 1
                if not self.retry_on_failure or attempt >= self.max_retries:
                    print("【终止】已达到最大重试次数或关闭重试功能。")
                    self.stop()
                    break
                print(f"【重试中】将在 {self.retry_delay_seconds} 秒后重试...")
                time.sleep(self.retry_delay_seconds)

    def _sendHeartbeat(self):
        while True:
            try:
                heartbeat = PushFrame(payload_type='hb').SerializeToString()
                self.ws.send(heartbeat, websocket.ABNF.OPCODE_PING)
                print("【√】发送心跳包")
            except Exception as e:
                print("【X】心跳包检测错误: ", e)
                break
            else:
                time.sleep(self.heartbeat_interval)

    
    def _wsOnOpen(self, ws):
        """
        连接建立成功
        """
        print("【√】WebSocket连接成功.")
        threading.Thread(target=self._sendHeartbeat).start()
    
    def _wsOnMessage(self, ws, message):
        """
        接收到数据
        :param ws: websocket实例
        :param message: 数据
        """
        # 解析proto结构体
        package = PushFrame().parse(message)
        response = Response().parse(gzip.decompress(package.payload))

        # 返回ack确认消息
        if response.need_ack:
            ack = PushFrame(
                log_id=package.log_id,
                payload_type='ack',
                payload=response.internal_ext.encode('utf-8')
            ).SerializeToString()
            ws.send(ack, websocket.ABNF.OPCODE_BINARY)

        # 加载消息处理映射
        dispatch_map = self.load_message_handlers()

        # 分发处理每条消息
        for msg in response.messages_list:
            method = msg.method
            handler = dispatch_map.get(method)
            if handler:
                try:
                    handler(msg.payload)
                except Exception as e:
                    print(f"【处理失败】{method}: {e}")

    def log_message(self, filename, headers, row):
        if self.rotate_daily:
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = f"{date_str}_{filename}"
        filepath = os.path.join(self.log_folder, f"{filename}.{self.log_format}")

        file_exists = os.path.isfile(filepath)
        with open(filepath, mode="a", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(headers)
            writer.writerow(row)

    
    def _wsOnError(self, ws, error):
        print("WebSocket error: ", error)
    
    def _wsOnClose(self, ws, *args):
        self.get_room_status()
        print("WebSocket connection closed.")
    
    def _parseChatMsg(self, payload):
        """聊天消息"""
        try:
            message = ChatMessage().parse(payload)
            user_name = message.user.nick_name
            user_id = message.user.id
            content = message.content

            cfg = self.handler_config.get("WebcastChatMessage", {})
            show_user_id = cfg.get("show_user_id", True)
            show_fans_club = cfg.get("show_fans_club", True)
            show_pay_grade = cfg.get("show_pay_grade", True)
            log_to_csv = cfg.get("log_to_csv", False)

            fans_club = None
            pay_grade = None
            if message.user:
                if show_fans_club and hasattr(message.user, 'fans_club') and message.user.fans_club and hasattr(message.user.fans_club, 'data') and message.user.fans_club.data:
                    fans_club = message.user.fans_club.data.level
                if show_pay_grade and hasattr(message.user, 'pay_grade') and message.user.pay_grade:
                    pay_grade = message.user.pay_grade.level

            # 显示记录
            display_parts = []
            if show_fans_club:
                display_parts.append(f"[{fans_club}]")
            if show_pay_grade:
                display_parts.append(f"[{pay_grade}]")
            if show_user_id and user_id != 111111:
                display_parts.append(f"[{user_id}]{user_name}")
            else:
                display_parts.append(user_name)

            print(f"【聊天msg】{' '.join(display_parts)}: {content}")

            # CSV 记录
            if log_to_csv:
                headers = ["timestamp", "user_id", "user_name", "fans_club", "pay_grade", "content"]
                row = [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S") if self.include_timestamp else "",
                    user_id if show_user_id else "",
                    user_name,
                    fans_club if show_fans_club else "",
                    pay_grade if show_pay_grade else "",
                    content
                ]
                self.log_message("chat_log", headers, row)


            return message
        except Exception as e:
            print(f"【聊天msg】解析失败: {e}")
            return None


    def _parseGiftMsg(self, payload):
        """礼物消息"""
        try:
            message = GiftMessage().parse(payload)
            user_name = message.user.nick_name
            gift_name = message.gift.name
            gift_cnt = message.combo_count
            gift_value = message.gift.diamond_count * gift_cnt

            cfg = self.handler_config.get("WebcastGiftMessage", {})
            track_total = cfg.get("track_total_diamonds", False)
            log_to_csv = cfg.get("log_to_csv", False)
            show_gift_value = cfg.get("show_gift_value", True)

            fans_club = None
            pay_grade = None
            if message.user:
                if hasattr(message.user, 'fans_club') and message.user.fans_club and hasattr(message.user.fans_club, 'data') and message.user.fans_club.data:
                    fans_club = message.user.fans_club.data.level
                if hasattr(message.user, 'pay_grade') and message.user.pay_grade:
                    pay_grade = message.user.pay_grade.level

            # 显示
            value_str = f"(价值: {gift_value})" if show_gift_value else ""
            print(f"【礼物msg】[{fans_club}] [{pay_grade}]|{user_name} 送出了 {gift_name}x{gift_cnt} {value_str}")

            # 总钻
            if track_total:
                self.total_diamonds += gift_value
                print(f"💎 当前累计钻石数: {self.total_diamonds}")

            # csv记录
            if log_to_csv:
                headers = ["timestamp", "user_name", "gift_name", "gift_count", "gift_value", "fans_club", "pay_grade"]
                row = [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S") if self.include_timestamp else "",
                    user_name,
                    gift_name,
                    gift_cnt,
                    gift_value if show_gift_value else "",
                    fans_club,
                    pay_grade
                ]
                self.log_message("gift_log", headers, row)

            return message
        except Exception as e:
            print(f"【礼物msg】解析失败: {e}")
            return None
            return None

    def _parseLikeMsg(self, payload):
        '''点赞消息'''
        message = LikeMessage().parse(payload)
        user_name = message.user.nick_name
        count = message.count
        print(f"【点赞msg】{user_name} 点了{count}个赞")
    
    def _parseMemberMsg(self, payload):
        """进入直播间消息"""
        try:
            message = MemberMessage().parse(payload)
            user_name = message.user.nick_name
            user_id = message.user.id

            #添加未知性别
            gender_map = ["女", "男"]
            gender_index = message.user.gender
            gender = gender_map[gender_index] if gender_index in [0, 1] else "未知"

            #匿名不显示id
            if user_id == 111111:
                print(f"【进场msg】[{gender}]{user_name} 进入了直播间")
            else:
                print(f"【进场msg】[{user_id}][{gender}]{user_name} 进入了直播间")
            return message
        except Exception as e:
            print(f"【进场msg】解析失败: {e}")
            return None
    
    def _parseSocialMsg(self, payload):
        '''关注消息'''
        message = SocialMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        print(f"【关注msg】[{user_id}]{user_name} 关注了主播")
    
    def _parseRoomUserSeqMsg(self, payload):
        """直播间统计"""
        message = RoomUserSeqMessage().parse(payload)
        current = message.total
        total_raw = message.total_pv_for_anchor
        total = parse_chinese_number(total_raw)

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        print(f"【统计msg】当前观看人数: {current}, 累计观看人数: {total}")

        cfg = self.handler_config.get("WebcastRoomUserSeqMessage", {})
        interval = cfg.get("log_interval_seconds", 300)
        log_to_csv = cfg.get("log_to_csv", False)

        if hasattr(self, "last_logged_time") and (now - self.last_logged_time).total_seconds() < interval:
            return
        self.last_logged_time = now

        if log_to_csv:
            headers = ["timestamp", "user_name", "gift_name", "gift_count", "gift_value", "fans_club", "pay_grade"]
            row = [
                timestamp if self.include_timestamp else "",
                "viewer_stats",  
                "viewer_count",  
                current,      
                total,       
                "",              
                ""               
            ]
            self.log_message("gift_log", headers, row)

    def _parseFansclubMsg(self, payload):
        '''粉丝团消息'''
        message = FansclubMessage().parse(payload)
        content = message.content
        print(f"【粉丝团msg】 {content}")
    
    def _parseEmojiChatMsg(self, payload):
        '''聊天表情包消息'''
        message = EmojiChatMessage().parse(payload)
        emoji_id = message.emoji_id
        user = message.user
        common = message.common
        default_content = message.default_content
        print(f"【聊天表情包id】 {emoji_id},user：{user},common:{common},default_content:{default_content}")
    
    def _parseRoomMsg(self, payload):
        message = RoomMessage().parse(payload)
        common = message.common
        room_id = common.room_id
        print(f"【直播间msg】直播间id:{room_id}")
    
    def _parseRoomStatsMsg(self, payload):
        message = RoomStatsMessage().parse(payload)
        display_long = message.display_long
        print(f"【直播间统计msg】{display_long}")
    
    def _parseRankMsg(self, payload):
        message = RoomRankMessage().parse(payload)
        ranks_list = message.ranks_list
        print(f"【直播间排行榜msg】{ranks_list}")
    
    def _parseControlMsg(self, payload):
        '''直播间状态消息'''
        message = ControlMessage().parse(payload)
        
        if message.status == 3:
            print("直播间已结束")
            self.stop()
    
    def _parseRoomStreamAdaptationMsg(self, payload):
        message = RoomStreamAdaptationMessage().parse(payload)
        adaptationType = message.adaptation_type
        print(f'直播间adaptation: {adaptationType}')
