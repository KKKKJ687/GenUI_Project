
from src.models.protocols import MQTT, Modbus

class Binding:
    def __init__(self, panel, protocols):
        self.panel = panel
        self.protocols = protocols

    def sync_data(self):
        return sync_protocols(self.protocols)

def create_binding(panel, protocols):
    return Binding(panel, protocols)

def validate_binding(binding):
    # Simple validation: ensure each protocol is valid
    return all(isinstance(protocol, (MQTT, Modbus)) for protocol in binding.protocols)
