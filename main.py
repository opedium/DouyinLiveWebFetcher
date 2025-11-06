#!/usr/bin/python
# coding:utf-8

# @FileName:    main.py
# @Time:        2024/1/2 22:27
# @Author:      bubu
# @Project:     douyinLiveWebFetcher

import yaml
from liveMan import DouyinLiveWebFetcher

if __name__ == '__main__':
    with open("message_handlers.yml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    live_id = config.get("live_id", "default_id")  # fallback if missing
    room = DouyinLiveWebFetcher(live_id)
    room.start()
