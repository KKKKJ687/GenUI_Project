"""
Protocol Simulators for Industrial Communication.

Provides mock implementations of MQTT and Modbus protocols
for testing and CI/CD without real hardware.

Phase 4 Implementation: 工业协议模拟器
"""
import json
import time
import logging
import threading
from typing import Dict, Any, Callable, Optional, List
from dataclasses import dataclass, field
from queue import Queue

logger = logging.getLogger(__name__)


# ===========================================
# Base Protocol Interface
# ===========================================

@dataclass
class ProtocolMessage:
    """Standard message format for all protocols."""
    topic: str
    payload: Any
    timestamp: float = field(default_factory=time.time)
    qos: int = 0
    
    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "qos": self.qos
        }


class ProtocolSimulatorBase:
    """Base class for protocol simulators."""
    
    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.messages: List[ProtocolMessage] = []
        self._callbacks: Dict[str, List[Callable]] = {}
    
    def connect(self) -> bool:
        """Simulate connection."""
        self.connected = True
        logger.info(f"{self.name} simulator connected")
        return True
    
    def disconnect(self):
        """Simulate disconnection."""
        self.connected = False
        logger.info(f"{self.name} simulator disconnected")
    
    def subscribe(self, topic: str, callback: Callable):
        """Subscribe to topic with callback."""
        if topic not in self._callbacks:
            self._callbacks[topic] = []
        self._callbacks[topic].append(callback)
    
    def publish(self, topic: str, payload: Any, qos: int = 0) -> bool:
        """Publish message to topic."""
        if not self.connected:
            return False
        
        msg = ProtocolMessage(topic=topic, payload=payload, qos=qos)
        self.messages.append(msg)
        
        # Trigger callbacks for matching subscriptions
        for pattern, callbacks in self._callbacks.items():
            if self._topic_matches(pattern, topic):
                for cb in callbacks:
                    try:
                        cb(msg)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
        
        return True
    
    def _topic_matches(self, pattern: str, topic: str) -> bool:
        """Check if topic matches subscription pattern."""
        # Simple wildcard matching
        if pattern == "#" or pattern == topic:
            return True
        if pattern.endswith("/#"):
            prefix = pattern[:-2]
            return topic.startswith(prefix)
        if "+" in pattern:
            # Single-level wildcard
            pattern_parts = pattern.split("/")
            topic_parts = topic.split("/")
            if len(pattern_parts) != len(topic_parts):
                return False
            for p, t in zip(pattern_parts, topic_parts):
                if p != "+" and p != t:
                    return False
            return True
        return False
    
    def get_message_log(self) -> List[dict]:
        """Get all messages for replay/audit."""
        return [m.to_dict() for m in self.messages]


# ===========================================
# MQTT Simulator
# ===========================================

class MQTTSimulator(ProtocolSimulatorBase):
    """
    MQTT Protocol Simulator.
    
    Simulates an MQTT broker for testing HMI components
    without requiring a real broker like Mosquitto.
    """
    
    def __init__(self, broker: str = "localhost", port: int = 1883):
        super().__init__("MQTT")
        self.broker = broker
        self.port = port
        self.client_id = f"genui_sim_{int(time.time())}"
        self.retained_messages: Dict[str, ProtocolMessage] = {}
    
    def publish(self, topic: str, payload: Any, qos: int = 0, retain: bool = False) -> bool:
        """Publish with MQTT-specific options."""
        result = super().publish(topic, payload, qos)
        
        if result and retain:
            msg = ProtocolMessage(topic=topic, payload=payload, qos=qos)
            self.retained_messages[topic] = msg
            
        return result
    
    def subscribe(self, topic: str, callback: Callable, qos: int = 0):
        """Subscribe with QoS option."""
        super().subscribe(topic, callback)
        
        # Deliver retained messages
        for t, msg in self.retained_messages.items():
            if self._topic_matches(topic, t):
                try:
                    callback(msg)
                except Exception as e:
                    logger.error(f"Retained message callback error: {e}")
    
    def simulate_sensor_data(self, topic: str, min_val: float, max_val: float, interval: float = 1.0):
        """
        Simulate periodic sensor data publication.
        
        Useful for testing gauge/chart widgets.
        """
        import random
        
        def _publish_loop():
            while self.connected:
                value = random.uniform(min_val, max_val)
                self.publish(topic, {"value": value, "unit": "V"})
                time.sleep(interval)
        
        thread = threading.Thread(target=_publish_loop, daemon=True)
        thread.start()
        return thread


# ===========================================
# Modbus Simulator
# ===========================================

class ModbusSimulator(ProtocolSimulatorBase):
    """
    Modbus Protocol Simulator.
    
    Simulates a Modbus RTU/TCP slave device with
    holding registers and coils for testing.
    """
    
    def __init__(self, slave_id: int = 1, host: str = "localhost", port: int = 502):
        super().__init__("Modbus")
        self.slave_id = slave_id
        self.host = host
        self.port = port
        
        # Modbus register banks
        self.holding_registers: Dict[int, int] = {}  # 40001-49999
        self.input_registers: Dict[int, int] = {}    # 30001-39999
        self.coils: Dict[int, bool] = {}             # 00001-09999
        self.discrete_inputs: Dict[int, bool] = {}   # 10001-19999
        
        # Initialize some default registers
        for i in range(10):
            self.holding_registers[i] = 0
            self.input_registers[i] = 0
            self.coils[i] = False
            self.discrete_inputs[i] = False
    
    def read_holding_register(self, address: int, count: int = 1) -> List[int]:
        """Read holding registers (Function Code 03)."""
        return [self.holding_registers.get(address + i, 0) for i in range(count)]
    
    def write_holding_register(self, address: int, value: int) -> bool:
        """Write single holding register (Function Code 06)."""
        if not self.connected:
            return False
        
        self.holding_registers[address] = value & 0xFFFF  # 16-bit
        
        # Log as message for audit
        self.publish(f"modbus/hr/{address}", {"value": value})
        return True
    
    def write_multiple_registers(self, address: int, values: List[int]) -> bool:
        """Write multiple holding registers (Function Code 16)."""
        for i, v in enumerate(values):
            self.holding_registers[address + i] = v & 0xFFFF
        return True
    
    def read_coil(self, address: int) -> bool:
        """Read single coil (Function Code 01)."""
        return self.coils.get(address, False)
    
    def write_coil(self, address: int, value: bool) -> bool:
        """Write single coil (Function Code 05)."""
        if not self.connected:
            return False
        
        self.coils[address] = value
        self.publish(f"modbus/coil/{address}", {"value": value})
        return True
    
    def simulate_input_register(self, address: int, min_val: int, max_val: int, interval: float = 1.0):
        """Simulate varying input register (sensor simulation)."""
        import random
        
        def _update_loop():
            while self.connected:
                self.input_registers[address] = random.randint(min_val, max_val)
                time.sleep(interval)
        
        thread = threading.Thread(target=_update_loop, daemon=True)
        thread.start()
        return thread


# ===========================================
# Protocol Manager (Unified Interface)
# ===========================================

class ProtocolManager:
    """
    Unified protocol manager for HMI runtime.
    
    Handles connection management and message routing
    for multiple protocol types.
    """
    
    def __init__(self):
        self.simulators: Dict[str, ProtocolSimulatorBase] = {}
        self._event_log: List[dict] = []
    
    def add_mqtt(self, name: str = "mqtt", broker: str = "localhost", port: int = 1883) -> MQTTSimulator:
        """Add MQTT simulator instance."""
        sim = MQTTSimulator(broker, port)
        self.simulators[name] = sim
        return sim
    
    def add_modbus(self, name: str = "modbus", slave_id: int = 1) -> ModbusSimulator:
        """Add Modbus simulator instance."""
        sim = ModbusSimulator(slave_id)
        self.simulators[name] = sim
        return sim
    
    def connect_all(self) -> Dict[str, bool]:
        """Connect all registered simulators."""
        results = {}
        for name, sim in self.simulators.items():
            results[name] = sim.connect()
        return results
    
    def disconnect_all(self):
        """Disconnect all simulators."""
        for sim in self.simulators.values():
            sim.disconnect()
    
    def get_simulator(self, name: str) -> Optional[ProtocolSimulatorBase]:
        """Get simulator by name."""
        return self.simulators.get(name)
    
    def get_session_log(self) -> List[dict]:
        """Get combined message log from all simulators."""
        all_messages = []
        for name, sim in self.simulators.items():
            for msg in sim.get_message_log():
                msg["protocol"] = name
                all_messages.append(msg)
        
        # Sort by timestamp
        all_messages.sort(key=lambda m: m.get("timestamp", 0))
        return all_messages
    
    def replay_session(self, log: List[dict], speed: float = 1.0):
        """
        Replay a recorded session.
        
        Args:
            log: List of message dicts from get_session_log()
            speed: Playback speed multiplier (1.0 = real-time)
        """
        if not log:
            return
        
        start_time = log[0].get("timestamp", 0)
        
        for msg in log:
            # Wait for appropriate time
            msg_time = msg.get("timestamp", 0)
            delay = (msg_time - start_time) / speed
            if delay > 0:
                time.sleep(delay)
            start_time = msg_time
            
            # Replay message
            protocol = msg.get("protocol", "mqtt")
            sim = self.simulators.get(protocol)
            if sim:
                sim.publish(msg["topic"], msg["payload"], msg.get("qos", 0))


# ===========================================
# Convenience Functions
# ===========================================

def create_test_environment() -> ProtocolManager:
    """
    Create a standard test environment with MQTT and Modbus simulators.
    
    Returns:
        Configured ProtocolManager ready for testing
    """
    manager = ProtocolManager()
    manager.add_mqtt("mqtt")
    manager.add_modbus("modbus")
    manager.connect_all()
    return manager
