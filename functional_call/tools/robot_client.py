from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from src.sr_modbus_sdk import SRModbusSdk
from src.sr_modbus_model import MovementState, MovementResult, ActionState, ActionResult


logger = logging.getLogger(__name__)


EventEmitter = Callable[[str, dict | None], None]


class RobotClient:
    """
    单机器人客户端（线程安全：每次Modbus交互用锁保护）。

    说明：
    - 只实现当前语音控制必需的指令/查询
    - 进度播报通过 emit(type, data) 由上层转成 VoiceEvent
    """

    def __init__(self, host: str, port: int) -> None:
        self._lock = threading.RLock()
        self._sdk = SRModbusSdk()
        self._host = host
        self._port = port
        self._sdk.connect_tcp(host, port)
        self._task_no = random.randint(1, 10000)

    def _next_task_no(self) -> int:
        self._task_no += 1
        return self._task_no

    # ------------------ 查询类 ------------------
    def get_battery_info(self) -> dict:
        with self._lock:
            b = self._sdk.get_battery_info()
        return {
            "percentage_electricity": b.percentage_electricity,
            "temperature": b.temperature,
            "state": str(b.state),
            "voltage": b.voltage,
            "current": b.current,
            "nominal_capacity": b.nominal_capacity,
            "use_cycles": b.use_cycles,
        }

    def get_movement_task_info(self) -> dict:
        with self._lock:
            t = self._sdk.get_movement_task_info()
        return {
            "state": str(t.state),
            "no": t.no,
            "target_station": t.target_station,
            "path_no": t.path_no,
            "result": str(t.result),
            "result_value": t.result_value,
        }

    def get_action_task_info(self) -> dict:
        with self._lock:
            t = self._sdk.get_action_task_info()
        return {
            "state": str(t.state),
            "no": t.no,
            "id": t.id,
            "param0": t.param0,
            "param1": t.param1,
            "result": str(t.result),
            "result_value": t.result_value,
        }

    def is_charging(self) -> bool:
        with self._lock:
            return bool(self._sdk.is_charge())

    # ------------------ 指令类（长耗时） ------------------
    def cancel_current_task(self) -> None:
        with self._lock:
            self._sdk.cancel_task()

    def move_to_station(
        self, 
        station_no: int, 
        *, 
        timeout_s: int = 120, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)

        # 如果已有任务在跑，先尝试取消
        try:
            info = self.get_movement_task_info()
            if "MT_RUNNING" in info["state"]:
                emit("progress", {"text": f"检测到机器人正在执行其他导航任务（任务号 {info['no']}），尝试取消旧任务。"})
                try:
                    self.cancel_current_task()
                except Exception as e:
                    emit("progress", {"text": f"取消旧任务失败：{e}，将继续尝试下发新任务。"})
        except Exception:
            pass

        task_no = self._next_task_no()
        emit("started", {"text": f"开始导航到站点 {station_no}（任务号 {task_no}）。"})

        with self._lock:
            self._sdk.move_to_station_no(station_no, task_no)

        start = time.time()
        last_progress_emit = 0.0
        while True:
            # 优先检查停止信号
            if stop_event and stop_event.is_set():
                logger.info("⏹️ 导航任务收到中断信号，正在取消机器人底层任务...")
                self.cancel_current_task()
                raise InterruptedError("导航任务已被取消")

            elapsed = int(time.time() - start)
            if elapsed >= timeout_s:
                raise TimeoutError(f"导航到站点{station_no}超时（已等待{timeout_s}秒）")

            # 每秒读取一次状态
            with self._lock:
                t = self._sdk.get_movement_task_info()

            # 进度播报节流：每5秒一次
            if time.time() - last_progress_emit >= 5:
                last_progress_emit = time.time()
                emit(
                    "progress",
                    {
                        "text": f"导航进行中，已等待 {elapsed} 秒，状态：{t.state}，当前任务号：{t.no}。",
                        "state": str(t.state),
                        "task_no": t.no,
                        "elapsed_s": elapsed,
                    },
                )

            if t.state == MovementState.MT_FINISHED and (t.no == task_no or task_no == 0):
                if t.result == MovementResult.MT_TASK_ERROR:
                    raise RuntimeError(f"导航任务失败：错误码 {t.result_value}")
                emit("step_done", {"text": f"已到达站点 {station_no}，耗时 {elapsed} 秒。"})
                return

            time.sleep(1)

    def execute_action(
        self,
        action_id: int,
        param1: int,
        param2: int,
        *,
        timeout_s: int = 60,
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        task_no = self._next_task_no()
        emit("started", {"text": f"开始执行动作（action_id={action_id}, param1={param1}, param2={param2}，任务号 {task_no}）。"})

        with self._lock:
            self._sdk.start_action_task_no(action_id, param1, param2, task_no)

        start = time.time()
        last_progress_emit = 0.0
        while True:
            if stop_event and stop_event.is_set():
                logger.info("⏹️ 动作任务收到中断信号，正在取消机器人底层任务...")
                self.cancel_current_task()
                raise InterruptedError("动作任务已被取消")

            elapsed = int(time.time() - start)
            if elapsed >= timeout_s:
                raise TimeoutError(f"动作任务超时（已等待{timeout_s}秒）")

            with self._lock:
                t = self._sdk.get_action_task_info()

            if time.time() - last_progress_emit >= 5:
                last_progress_emit = time.time()
                emit(
                    "progress",
                    {
                        "text": f"动作执行中，已等待 {elapsed} 秒，状态：{t.state}，当前任务号：{t.no}。",
                        "state": str(t.state),
                        "task_no": t.no,
                        "elapsed_s": elapsed,
                    },
                )

            if t.state == ActionState.AT_FINISHED and (t.no == task_no or task_no == 0):
                if t.result == ActionResult.AT_TASK_ERROR:
                    raise RuntimeError(f"动作任务失败：错误码 {t.result_value}")
                emit("step_done", {"text": f"动作执行完成，耗时 {elapsed} 秒。"})
                return

            time.sleep(1)

    def start_charge(
        self, 
        *, 
        timeout_s: int = 60, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        
        logger.info("正在检查当前充电状态...")
        charging_now = self.is_charging()
        logger.info(f"当前充电状态: {charging_now}")
        
        if charging_now:
            emit("step_done", {"text": "机器人已在充电中。"})
            return
            
        emit("started", {"text": "开始启动充电。"})
        logger.info(">>> 向 Modbus 发送充电线圈(9)写指令...")
        with self._lock:
            self._sdk.charge()
        logger.info(">>> 充电指令发送完成，开始轮询确认状态...")
        
        start = time.time()
        while True:
            if stop_event and stop_event.is_set():
                logger.info("⏹️ 充电轮询收到中断信号")
                raise InterruptedError("充电任务已被取消")

            elapsed = int(time.time() - start)
            current_status = self.is_charging()
            
            logger.info(f"[轮询中] 已等待 {elapsed}s, is_charging={current_status}")
            
            if current_status:
                logger.info(f"✅ 成功检测到充电状态转变！耗时 {elapsed}s")
                emit("step_done", {"text": f"充电已启动（耗时 {elapsed} 秒）。"})
                return
            
            if elapsed >= timeout_s:
                logger.error(f"❌ 充电确认超时！已等待 {timeout_s}s 状态仍未改变。")
                raise TimeoutError(f"启动充电超时（已等待{timeout_s}秒）")
            
            if elapsed % 5 == 0:
                emit("progress", {"text": f"等待充电启动中，已等待 {elapsed} 秒。", "elapsed_s": elapsed})
            
            time.sleep(1)

    def stop_charge(
        self, 
        *, 
        timeout_s: int = 60, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        emit = emit or (lambda _t, _d=None: None)
        if not self.is_charging():
            emit("step_done", {"text": "机器人当前未在充电。"})
            return
        emit("started", {"text": "开始停止充电。"})
        with self._lock:
            self._sdk.stop_charge()
        start = time.time()
        while True:
            if stop_event and stop_event.is_set():
                logger.info("⏹️ 停止充电轮询收到中断信号")
                raise InterruptedError("停止充电任务已被取消")

            elapsed = int(time.time() - start)
            if not self.is_charging():
                emit("step_done", {"text": f"充电已停止（耗时 {elapsed} 秒）。"})
                return
            if elapsed >= timeout_s:
                raise TimeoutError(f"停止充电超时（已等待{timeout_s}秒）")
            if elapsed % 5 == 0:
                emit("progress", {"text": f"等待充电停止中，已等待 {elapsed} 秒。", "elapsed_s": elapsed})
            time.sleep(1)
