from enum import Enum
from typing import List, Optional, Union, Literal, Annotated, Dict, Any
from pydantic import BaseModel, Field, model_validator, ConfigDict

# ==========================================
# 1. Enums & Constants
# ==========================================
class WidgetType(str, Enum):
    SLIDER = "slider"
    SWITCH = "switch"
    GAUGE = "gauge"
    INPUT = "input"      # ✅ 修复1: 补全 Input 类型
    SELECT = "select"
    RADIO = "radio"
    PLOT = "plot"

class ProtocolType(str, Enum):
    MQTT = "mqtt"
    MODBUS = "modbus"
    HTTP = "http"
    MOCK = "mock"

class ThemeType(str, Enum):
    DARK = "dark"
    LIGHT = "light"
    INDUSTRIAL_BLUE = "industrial_blue"

# ==========================================
# 2. Safety & Binding
# ==========================================
class SafetyPolicy(BaseModel):
    max_value: Optional[float] = Field(None)
    min_value: Optional[float] = Field(None)
    forbidden_values: List[float] = Field(default_factory=list)
    requires_confirmation: bool = Field(False)
    unit: str = Field("unitless")

class DataBinding(BaseModel):
    protocol: ProtocolType = Field(ProtocolType.MOCK)
    # Backward/forward compatibility:
    # newer payloads may only provide topic/register.
    address: str = Field("")

    # ✅ 修复2: 兼容 Prompt 生成的 "access" 字段名和 "read/write" 值
    # 使用 alias="access" 让它能读取 "access" 字段
    # 允许 "read", "write" 并在验证后自动转为标准格式 (可选)
    access_mode: Literal["r", "w", "rw", "read", "write"] = Field("rw", alias="access")

    # Protocol endpoint details (Phase 4/5):
    # keep all optional for backward compatibility with old payloads.
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    topic: Optional[str] = None
    modbus_register: Optional[int] = Field(None, alias="register", ge=0)
    qos: int = Field(0, ge=0, le=2)
    client_id: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False

    update_interval_ms: int = Field(1000)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_endpoint_fields(cls, data):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        address = normalized.get("address")
        topic = normalized.get("topic")
        register = normalized.get("register", normalized.get("modbus_register"))

        if (address is None or str(address).strip() == "") and topic:
            normalized["address"] = str(topic)
        elif (address is None or str(address).strip() == "") and register is not None:
            normalized["address"] = str(register)

        return normalized

    @model_validator(mode="after")
    def validate_protocol_specific_fields(self):
        # Keep address as canonical fallback for old payloads.
        if self.protocol == ProtocolType.MQTT:
            if not self.topic:
                self.topic = self.address
            if (not self.address) and self.topic:
                self.address = self.topic
            if not self.topic:
                raise ValueError("MQTT binding requires 'topic' or 'address'")
            if self.port is None:
                self.port = 1883
            if not self.host:
                self.host = "localhost"

        if self.protocol == ProtocolType.MODBUS:
            if self.modbus_register is None:
                try:
                    self.modbus_register = int(self.address)
                except (TypeError, ValueError):
                    self.modbus_register = None
            if self.modbus_register is None:
                raise ValueError("Modbus binding requires numeric 'register' or numeric 'address'")
            if not self.address:
                self.address = str(self.modbus_register)
            if self.port is None:
                self.port = 502
            if not self.host:
                self.host = "localhost"

        return self

# ==========================================
# 3. Layout System
# ==========================================
class LayoutItem(BaseModel):
    i: str = Field(...)
    x: int = Field(0)
    y: int = Field(0)
    w: int = Field(1)
    h: int = Field(1)

# ==========================================
# 4. Widget Definitions
# ==========================================
class BaseWidget(BaseModel):
    id: str = Field(...)
    label: str = Field(...)
    description: Optional[str] = Field(None)
    unit: Optional[str] = Field(None)
    disabled: bool = False
    binding: Optional[DataBinding] = None
    safety: Optional[SafetyPolicy] = None
    
    model_config = ConfigDict(extra="forbid")

class SliderWidget(BaseWidget):
    type: Literal[WidgetType.SLIDER] = WidgetType.SLIDER
    min: float = 0.0
    max: float = 100.0
    step: float = 1.0
    value: float = 0.0
    vertical: bool = False

    @model_validator(mode='after')
    def validate_slider_range(self):
        if self.min > self.max:
            raise ValueError(f"Slider '{self.id}' has invalid range: min ({self.min}) > max ({self.max})")
        if self.value < self.min or self.value > self.max:
            raise ValueError(
                f"Slider '{self.id}' value {self.value} out of range [{self.min}, {self.max}]"
            )
        return self

class SwitchWidget(BaseWidget):
    type: Literal[WidgetType.SWITCH] = WidgetType.SWITCH
    on_label: str = "ON"
    off_label: str = "OFF"
    value: bool = False
    color_on: str = "green"

class GaugeWidget(BaseWidget):
    type: Literal[WidgetType.GAUGE] = WidgetType.GAUGE
    min: float = 0.0
    max: float = 100.0
    value: float = 0.0
    thresholds: List[float] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_gauge_range(self):
        if self.min > self.max:
            raise ValueError(f"Gauge '{self.id}' has invalid range: min ({self.min}) > max ({self.max})")
        if self.value < self.min or self.value > self.max:
            raise ValueError(
                f"Gauge '{self.id}' value {self.value} out of range [{self.min}, {self.max}]"
            )
        return self

# ✅ 修复3: 定义 InputWidget 并加入联合类型
class InputWidget(BaseWidget):
    type: Literal[WidgetType.INPUT] = WidgetType.INPUT
    value: Union[float, str] = 0.0
    unit: Optional[str] = None
    input_type: Literal["number", "text"] = "number"
    # Allow bounded numeric input; LLM outputs often include these.
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None

    @model_validator(mode='after')
    def validate_input_bounds(self):
        if self.input_type == "number":
            try:
                num_val = float(self.value)
            except (TypeError, ValueError):
                raise ValueError(f"Input '{self.id}' expects numeric value, got: {self.value}")

            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(
                    f"Input '{self.id}' has invalid range: min ({self.min}) > max ({self.max})"
                )
            if self.min is not None and num_val < self.min:
                raise ValueError(
                    f"Input '{self.id}' value {num_val} below min {self.min}"
                )
            if self.max is not None and num_val > self.max:
                raise ValueError(
                    f"Input '{self.id}' value {num_val} above max {self.max}"
                )
        return self


class SelectWidget(BaseWidget):
    type: Literal[WidgetType.SELECT] = WidgetType.SELECT
    options: List[str] = Field(default_factory=list)
    value: Union[str, float, int] = ""

    @model_validator(mode='after')
    def validate_select_options(self):
        if not self.options:
            raise ValueError(f"Select '{self.id}' requires non-empty options")
        normalized_options = [str(x).strip() for x in self.options]
        value_text = str(self.value).strip()
        if value_text not in normalized_options:
            raise ValueError(
                f"Select '{self.id}' value '{self.value}' not in options {self.options}"
            )
        return self


class RadioWidget(BaseWidget):
    type: Literal[WidgetType.RADIO] = WidgetType.RADIO
    options: List[str] = Field(default_factory=list)
    value: Union[str, float, int] = ""

    @model_validator(mode='after')
    def validate_radio_options(self):
        if not self.options:
            raise ValueError(f"Radio '{self.id}' requires non-empty options")
        normalized_options = [str(x).strip() for x in self.options]
        value_text = str(self.value).strip()
        if value_text not in normalized_options:
            raise ValueError(
                f"Radio '{self.id}' value '{self.value}' not in options {self.options}"
            )
        return self

class PlotWidget(BaseWidget):
    type: Literal[WidgetType.PLOT] = WidgetType.PLOT
    title: str = "Chart"
    x_label: str = "Time"
    y_label: str = "Value"
    duration_seconds: int = 60
    # Many generated DSL payloads annotate telemetry chart bounds.
    # Keep optional to remain backward compatible and avoid hard parse failures.
    min: Optional[float] = None
    max: Optional[float] = None

# 注册所有组件类型
AnyWidget = Annotated[
    Union[SliderWidget, SwitchWidget, GaugeWidget, InputWidget, SelectWidget, RadioWidget, PlotWidget],
    Field(discriminator="type")
]

# ==========================================
# 5. Root Document
# ==========================================
class HMIPanel(BaseModel):
    version: str = Field("0.1")
    title: str = Field(..., alias="project_name") # 兼容 project_name
    description: Optional[str] = None
    theme: ThemeType = ThemeType.DARK
    widgets: List[AnyWidget] = Field(...)
    layout: List[LayoutItem] = Field(...)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_payload(cls, data):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        metadata = normalized.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        # Backward compatibility: old payloads may use "panels" for widgets.
        if "widgets" not in normalized and isinstance(normalized.get("panels"), list):
            normalized["widgets"] = normalized.get("panels")
        normalized.pop("panels", None)

        # Preserve LLM-added auxiliary records instead of failing hard on root extra fields.
        # Example: validation_report / audit notes can be stored in metadata for traceability.
        allowed_root = {
            "version",
            "title",
            "project_name",
            "description",
            "theme",
            "widgets",
            "layout",
            "metadata",
        }
        extra_root = {k: normalized[k] for k in list(normalized.keys()) if k not in allowed_root}
        if extra_root:
            # Keep deterministic merge order
            for k in sorted(extra_root.keys()):
                metadata[k] = extra_root[k]
                normalized.pop(k, None)
        normalized["metadata"] = metadata

        # Normalize common UI style names to strict schema theme enum values.
        theme = normalized.get("theme")
        if isinstance(theme, str):
            key = theme.strip().lower().replace("-", "_").replace(" ", "_")
            theme_aliases = {
                "dark_mode": ThemeType.DARK.value,
                "light_mode": ThemeType.LIGHT.value,
                "industrialblue": ThemeType.INDUSTRIAL_BLUE.value,
                "industrial_blue_mode": ThemeType.INDUSTRIAL_BLUE.value,
                "classic": ThemeType.LIGHT.value,
                "minimalist": ThemeType.LIGHT.value,
                "cyberpunk": ThemeType.DARK.value,
                "neon": ThemeType.DARK.value,
                "wizard_green": ThemeType.DARK.value,
            }
            canonical = theme_aliases.get(key, key)
            if canonical in ThemeType._value2member_map_:
                normalized["theme"] = canonical

        widgets = normalized.get("widgets")
        if isinstance(widgets, list):
            normalized_widgets = []
            for widget in widgets:
                if not isinstance(widget, dict):
                    normalized_widgets.append(widget)
                    continue
                widget_data = dict(widget)
                # Legacy payloads sometimes use 0/1 for switch boolean values.
                if (
                    str(widget_data.get("type", "")).strip().lower() == WidgetType.SWITCH.value
                    and isinstance(widget_data.get("value"), (int, float))
                    and widget_data.get("value") in (0, 1)
                ):
                    widget_data["value"] = bool(widget_data["value"])
                normalized_widgets.append(widget_data)
            normalized["widgets"] = normalized_widgets

        return normalized

    @model_validator(mode='after')
    def validate_layout_ids(self):
        widget_ids = {w.id for w in self.widgets}
        layout_ids = {item.i for item in self.layout}
        missing_layout = widget_ids - layout_ids
        orphan_layout = layout_ids - widget_ids
        if missing_layout or orphan_layout:
            raise ValueError(
                f"Layout/widget mismatch: missing_layout={sorted(missing_layout)}, orphan_layout={sorted(orphan_layout)}"
            )
        return self
