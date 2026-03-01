
class ProtocolBase:
    def __init__(self, config):
        self.config = config

    def get_data(self):
        raise NotImplementedError("This method should be implemented in subclasses")

class MQTT(ProtocolBase):
    def __init__(self, config):
        super().__init__(config)
        self.topic = config.get('topic')

    def get_data(self):
        return {"topic": self.topic}

class Modbus(ProtocolBase):
    def __init__(self, config):
        super().__init__(config)
        self.register = config.get('register')

    def get_data(self):
        return {"register": self.register}
