"""Tencent Cloud DNSPod DDNS client, migrated from the py-ddns project.

Keeps an ``A`` record of a domain in sync with the current public IP, polling
an IP server and updating the record through the DNSPod API when it changes.
"""

from __future__ import annotations

import json
import logging
import time
import traceback

import requests
from requests.exceptions import ConnectionError
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.dnspod.v20210323 import dnspod_client, models
from urllib3.exceptions import NameResolutionError

from eb_personal_kit.ddns.config import Settings

logger = logging.getLogger("ddns")

RECORD_NAME = "@"  # root domain record


class DDNS:
    """Poll the current public IP and keep the DNSPod record up to date."""

    def __init__(self, config: Settings):
        self.config = config
        cred = credential.Credential(
            config.tencentcloud_secret_id.get_secret_value(),
            config.tencentcloud_secret_key.get_secret_value(),
        )
        self.client = dnspod_client.DnspodClient(cred, "")
        self.params: dict[str, object] = {}

        self.record_ip = ""
        self.current_ip = ""

        self.record_id = ""
        self.record_type = "A"
        self.record_line = "default"
        self.record_line_id = ""

    def run(self) -> None:
        while True:
            sleep_min = 60
            time.sleep(1)
            try:
                self.get_current_ip()
                self.describe_record_list()
                if self.current_ip != self.record_ip:
                    logger.info(f"ip has changed from {self.record_ip} to {self.current_ip}")
                    self.modify_record()
            except TencentCloudSDKException as err:
                sleep_min = 15
                logger.error(f"sdk error: {err}, params: {self.params}")
                logger.error(traceback.format_exc())
            except NameResolutionError as err:
                sleep_min = 1
                logger.error(f"NameResolutionError: {err}")
                logger.error(traceback.format_exc())
            except ConnectionError as err:
                sleep_min = 1
                logger.error(f"ConnectionError: {err}")
                logger.error(traceback.format_exc())
            except Exception as e:  # noqa: BLE001 - keep the daemon loop alive
                logger.error(f"unknown exception occurred {e}")
                logger.error(traceback.format_exc())
            time.sleep(sleep_min * 60)

    def get_current_ip(self) -> None:
        resp = requests.get(self.config.ip_server, timeout=30)
        self.current_ip = resp.json()["ip"]
        logger.info(f"current ip: {self.current_ip}")

    def describe_record_list(self) -> None:
        # Instantiate a request object; each API call has a corresponding request object
        req = models.DescribeRecordListRequest()
        self.params = {"Domain": self.config.domain, "RecordType": self.record_type}
        logger.debug(f"DescribeRecordList request: {json.dumps(self.params, ensure_ascii=False)}")
        req.from_json_string(json.dumps(self.params))

        # The returned resp is a DescribeRecordListResponse instance matching the request
        resp = self.client.DescribeRecordList(req)
        # Log the JSON response payload
        logger.debug(f"DescribeRecordList response: {resp.to_json_string()}")

        for record in resp.RecordList:
            if record.Name == RECORD_NAME:
                self.record_ip = record.Value
                self.record_id = record.RecordId
                self.record_line = record.Line
                self.record_line_id = record.LineId

    def modify_record(self) -> None:
        # Instantiate a request object; each API call has a corresponding request object
        req = models.ModifyRecordRequest()
        self.params = {
            "Domain": self.config.domain,
            "RecordType": self.record_type,
            "RecordLine": self.record_line,
            "Value": self.current_ip,
            "RecordId": self.record_id,
            "RecordLineId": self.record_line_id,
        }
        logger.info(f"ModifyRecord request: {json.dumps(self.params, ensure_ascii=False)}")
        req.from_json_string(json.dumps(self.params))

        # The returned resp is a ModifyRecordResponse instance matching the request
        resp = self.client.ModifyRecord(req)
        # Log the JSON response payload
        logger.info(f"ModifyRecord response: {resp.to_json_string()}")
