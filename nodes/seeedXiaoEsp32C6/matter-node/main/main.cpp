#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/ledc.h"
#include "driver/temperature_sensor.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_endpoint.h"
#include "esp_matter_event.h"
#include "esp_matter_providers.h"
#include "esp_matter_test_event_trigger.h"
#include "esp_openthread.h"
#include "esp_openthread_types.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "platform/ESP32/OpenthreadLauncher.h"

#include "bms_node_core/device_info.h"
#include "bms_node_core/thread_diagnostics.h"

#include <app/server/Server.h>
#include <app/TestEventTriggerDelegate.h>
#include <credentials/DeviceAttestationCredsProvider.h>
#include <credentials/examples/DeviceAttestationCredsExample.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <clusters/BasicInformation/AttributeIds.h>
#include <clusters/BasicInformation/ClusterId.h>
#include <clusters/Switch/AttributeIds.h>
#include <clusters/Switch/ClusterId.h>
#include <clusters/TemperatureMeasurement/AttributeIds.h>
#include <clusters/TemperatureMeasurement/ClusterId.h>
#include <platform/ConnectivityManager.h>
#include <platform/PlatformManager.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include "qrcodegen.h"

#include <cmath>
#include <cstdio>
#include <cstring>

static const char *TAG = "bms_matter_node";
#ifndef MATTER_NODE_GIT_COMMIT
#define MATTER_NODE_GIT_COMMIT "unknown"
#endif
#ifndef MATTER_NODE_GIT_COMMIT_U32
#define MATTER_NODE_GIT_COMMIT_U32 0
#endif

constexpr bms_node_core::BoardIdentity kBoardIdentity = {
    .vendor_id            = 0xFFF1,
    .product_id           = 0x8004,
    .vendor_name          = "BMS DOA",
    .product_name         = "Seeed XIAO ESP32-C6",
    .serial_prefix        = "BMS-XC6-",
    .hw_version           = 1,
    .hw_version_str       = "v1.1",
    .software_version_str = MATTER_NODE_GIT_COMMIT,
    .provide_rotating_id  = true,
};

static bms_node_core::DeviceInfoProvider s_device_info_provider(kBoardIdentity);

using namespace esp_matter;
using namespace esp_matter::attribute;
using namespace esp_matter::endpoint;

namespace {

constexpr gpio_num_t kStatusLedGpio = GPIO_NUM_15;
constexpr gpio_num_t kInfraredReflectiveGpio = GPIO_NUM_0;
constexpr gpio_num_t kUserButtonGpio = GPIO_NUM_1;
constexpr gpio_num_t kExpansionBuzzerGpio = GPIO_NUM_21;
constexpr gpio_num_t kExpansionI2cSdaGpio = GPIO_NUM_22;
constexpr gpio_num_t kExpansionI2cSclGpio = GPIO_NUM_23;
constexpr i2c_port_t kExpansionI2cPort = I2C_NUM_0;
constexpr uint32_t kExpansionI2cClockHz = 100 * 1000;
constexpr uint8_t kOledCandidateAddrs[] = {0x3c, 0x3d};
constexpr uint8_t kBma400Addr = 0x15;
constexpr uint8_t kBma400ChipIdReg = 0x00;
constexpr uint8_t kBma400ExpectedChipId = 0x90;
constexpr uint8_t kBma400RegAccelData = 0x04;
constexpr uint8_t kBma400RegIntStat0 = 0x0e;
constexpr uint8_t kBma400RegAccelConfig0 = 0x19;
constexpr uint8_t kBma400RegIntConf1 = 0x20;
constexpr uint8_t kBma400RegIntMap = 0x21;
constexpr uint8_t kBma400RegTapConfig = 0x57;
constexpr uint8_t kBma400PowerModeMask = 0x03;
constexpr uint8_t kBma400PowerModeNormal = 0x02;
constexpr uint8_t kBma400AccelOdrMask = 0x0f;
constexpr uint8_t kBma400AccelOdr200Hz = 0x09;
constexpr uint8_t kBma400AccelOdr400Hz = 0x0a;
constexpr uint8_t kBma400AccelRangeMask = 0xc0;
constexpr uint8_t kBma400AccelRange16g = 0x03;
constexpr uint8_t kBma400AccelRangePos = 6;
constexpr uint8_t kBma400DataFilterMask = 0x0c;
constexpr uint8_t kBma400Filt1BwMask = 0x80;
constexpr uint8_t kBma400Filt1Bw1 = 0x01;
constexpr uint8_t kBma400Filt1BwPos = 7;
constexpr uint8_t kBma400TapAxesMask = 0x18;
constexpr uint8_t kBma400TapAxesXyz = 0x03;
constexpr uint8_t kBma400TapAxesPos = 3;
constexpr uint8_t kBma400TapSensitivityMask = 0x07;
constexpr uint8_t kBma400TapTimingMask = 0x3f;
constexpr uint8_t kBma400SingleTapEnableMask = 0x04;
constexpr uint8_t kBma400DoubleTapEnableMask = 0x08;
constexpr uint8_t kBma400TapMapInt1Mask = 0x04;
constexpr uint8_t kBma400IntStatusMask = 0xe0;
constexpr uint8_t kBma400IntStatusPos = 5;
constexpr uint16_t kBma400SingleTapAsserted = 0x0400;
constexpr uint16_t kBma400DoubleTapAsserted = 0x0800;
constexpr int kQrMaxVersion = 3;
constexpr int kQrScale = 2;
constexpr int kQrBorder = 1;
constexpr uint8_t kI2cProbeAddrs[] = {
    kBma400Addr, // Grove BMA400 default address.
    0x18, 0x19, // Common accelerometer addresses.
    0x1c, 0x1d, // Common accelerometer addresses.
    0x3c, 0x3d, // OLED.
    0x51,       // Expansion board RTC.
    0x53,       // ADXL345.
    0x68, 0x69, // IMU/RTC family.
    0x6a, 0x6b, // LSM6/IMU family.
};
constexpr int kOledWidth = 128;
constexpr int kOledHeight = 64;
constexpr int kOledPages = kOledHeight / 8;
constexpr uint32_t kTelemetryPeriodMs = 5000;
constexpr uint32_t kHeartbeatPeriodMs = 30000;
constexpr uint32_t kButtonPollPeriodMs = 50;
constexpr uint32_t kButtonDebounceMs = 30;
constexpr uint32_t kShortPressMaxMs = 1500;
constexpr uint32_t kInfraredPollPeriodMs = 50;
constexpr uint32_t kInfraredDebounceMs = 30;
constexpr uint32_t kBma400TapPollPeriodMs = 5;
constexpr uint32_t kBma400DoubleTapCooldownMs = 500;
constexpr uint32_t kBma400DoubleKnockMinGapMs = 80;
constexpr uint32_t kBma400DoubleKnockWindowMs = 700;
constexpr float kBma400KnockBaselineAlpha = 0.02f;
constexpr float kBma400KnockThreshold = 35.0f;
constexpr float kBma400KnockReleaseThreshold = 18.0f;
constexpr uint32_t kCommissioningHoldMs = 7000;
constexpr uint32_t kFactoryResetHoldMs = 15000;
constexpr uint32_t kCommissioningWindowSeconds = 180;
constexpr uint32_t kBootIndicatorMs = 5000;
constexpr uint32_t kIndicatorPeriodMs = 50;
constexpr uint32_t kAirRebootDelayMs = 500;
constexpr uint64_t kBmsAirRebootEventTrigger = 0xFFF10001ull;
constexpr uint8_t kLedBrightnessCap = 96;
constexpr ledc_mode_t kStatusLedSpeedMode = LEDC_LOW_SPEED_MODE;
constexpr ledc_timer_t kStatusLedTimer = LEDC_TIMER_0;
constexpr ledc_channel_t kStatusLedChannel = LEDC_CHANNEL_0;
constexpr ledc_timer_bit_t kStatusLedResolution = LEDC_TIMER_8_BIT;
constexpr uint32_t kStatusLedMaxDuty = 255;
constexpr ledc_timer_t kBuzzerTimer = LEDC_TIMER_1;
constexpr ledc_channel_t kBuzzerChannel = LEDC_CHANNEL_1;
constexpr ledc_timer_bit_t kBuzzerResolution = LEDC_TIMER_10_BIT;
constexpr uint32_t kBuzzerDuty = 256;
constexpr uint32_t kBuzzerToneHz = 2200;
constexpr uint32_t kButtonBeepMs = 80;

enum class IndicatorState : uint8_t {
    Boot,
    Commissioning,
    ThreadDetached,
    Running,
    CommissioningPreview,
    FactoryResetPreview,
    FactoryResetActive,
};

enum class DisplayPage : uint8_t {
    Overview,
    Infrared,
    Accelerometer,
    Temperature,
    I2c,
    Matter,
    System,
};

constexpr uint8_t kDisplayPageCount = 7;

struct DeviceContext {
    uint16_t temp_ep_id = 0;
    uint16_t button_ep_id = 0;
    temperature_sensor_handle_t temp_handle = nullptr;
    bool infrared_reflection = false;
    bool button_pressed = false;
    bool oled_ready = false;
    bool bma400_present = false;
    bool bma400_double_tap_ready = false;
    bool onboarding_qr_ready = false;
    DisplayPage display_page = DisplayPage::Overview;
    uint8_t oled_addr = 0;
    uint8_t bma400_chip_id = 0;
    uint16_t bma400_last_interrupt_status = 0;
    int16_t bma400_x = 0;
    int16_t bma400_y = 0;
    int16_t bma400_z = 0;
    uint16_t i2c_found_mask = 0;
    uint32_t i2c_timeout_count = 0;
    uint32_t i2c_fail_count = 0;
    uint32_t i2c_other_error_count = 0;
    uint32_t bma400_double_tap_count = 0;
    uint32_t bma400_last_double_tap_ms = 0;
    uint32_t bma400_tap_read_error_count = 0;
    float bma400_knock_signal = 0.0f;
    float bma400_knock_peak = 0.0f;
    size_t fabric_count = 0;
    esp_reset_reason_t reset_reason = ESP_RST_UNKNOWN;
    char onboarding_qr_code[256] = {};
    float last_temperature_c = NAN;
};

struct IndicatorCtx {
    volatile bool thread_attached = false;
    volatile bool commissioned = false;
    volatile bool window_open = false;
    volatile bool button_preview_commissioning = false;
    volatile bool button_preview_factory_reset = false;
    volatile bool factory_reset_active = false;
};

DeviceContext s_device;
IndicatorCtx s_indicator;
TickType_t s_boot_tick = 0;
SemaphoreHandle_t s_oled_mutex = nullptr;
uint8_t s_oled_buffer[kOledWidth * kOledPages] = {};
uint8_t s_test_event_enable_key[chip::TestEventTriggerDelegate::kEnableKeyLength] = {
    0x00, 0x11, 0x22, 0x33,
    0x44, 0x55, 0x66, 0x77,
    0x88, 0x99, 0xaa, 0xbb,
    0xcc, 0xdd, 0xee, 0xff,
};

void air_reboot_task(void *)
{
    vTaskDelay(pdMS_TO_TICKS(kAirRebootDelayMs));
    ESP_LOGW(TAG, "Rebooting after Matter air reboot request");
    esp_restart();
}

class BmsNodeTestEventTriggerHandler : public chip::TestEventTriggerHandler
{
public:
    CHIP_ERROR HandleEventTrigger(uint64_t eventTrigger) override
    {
        if (eventTrigger != kBmsAirRebootEventTrigger) {
            return CHIP_ERROR_INVALID_ARGUMENT;
        }
        ESP_LOGW(TAG, "Matter air reboot requested");
        xTaskCreate(air_reboot_task, "air_reboot_task", 2048, nullptr, 5, nullptr);
        return CHIP_NO_ERROR;
    }
};

chip::SimpleTestEventTriggerDelegate s_test_event_trigger_delegate;
BmsNodeTestEventTriggerHandler s_bms_test_event_trigger_handler;

// Matter spec: temperature in 0.01 °C units (int16_t)
int16_t to_matter_temp(float celsius) { return static_cast<int16_t>(celsius * 100.0f); }

esp_err_t ensure_serial_number_attribute()
{
    cluster_t *basic_info_cluster = cluster::get(static_cast<uint16_t>(0), chip::app::Clusters::BasicInformation::Id);
    if (!basic_info_cluster) {
        ESP_LOGE(TAG, "Basic Information cluster not found on root endpoint");
        return ESP_ERR_NOT_FOUND;
    }

    attribute_t *serial_attribute = attribute::get(
        0,
        chip::app::Clusters::BasicInformation::Id,
        chip::app::Clusters::BasicInformation::Attributes::SerialNumber::Id);
    if (serial_attribute) {
        ESP_LOGI(TAG, "Basic Information SerialNumber attribute already present");
        return ESP_OK;
    }

    serial_attribute = cluster::basic_information::attribute::create_serial_number(basic_info_cluster, nullptr, 0);
    if (!serial_attribute) {
        ESP_LOGE(TAG, "Failed to create Basic Information SerialNumber attribute");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Created Basic Information SerialNumber attribute (0/40/15)");
    return ESP_OK;
}

bool read_boot_button_pressed()
{
    return gpio_get_level(kUserButtonGpio) == 0;
}

bool read_infrared_reflection_detected()
{
    return gpio_get_level(kInfraredReflectiveGpio) == 0;
}

const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT";
    case ESP_RST_SW:        return "SW";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "OTHER_WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_SDIO:      return "SDIO";
    case ESP_RST_USB:       return "USB";
    case ESP_RST_JTAG:      return "JTAG";
    default:                return "UNKNOWN";
    }
}

const char *indicator_state_str(IndicatorState s)
{
    switch (s) {
    case IndicatorState::Boot:                  return "boot/white-pulse";
    case IndicatorState::Commissioning:         return "commissioning/blue-blink";
    case IndicatorState::ThreadDetached:        return "thread-detached/yellow";
    case IndicatorState::Running:               return "running/green";
    case IndicatorState::CommissioningPreview:  return "btn-preview-commissioning/blue-fast";
    case IndicatorState::FactoryResetPreview:   return "btn-preview-factory-reset/red-fast";
    case IndicatorState::FactoryResetActive:    return "factory-reset/red";
    }
    return "?";
}

const char *display_page_name(DisplayPage page)
{
    switch (page) {
    case DisplayPage::Overview:       return "OVERVIEW";
    case DisplayPage::Infrared:       return "IR SENSOR";
    case DisplayPage::Accelerometer:  return "ACCEL";
    case DisplayPage::Temperature:    return "TEMP";
    case DisplayPage::I2c:            return "I2C";
    case DisplayPage::Matter:         return "MATTER";
    case DisplayPage::System:         return "SYSTEM";
    }
    return "?";
}

uint8_t display_page_index(DisplayPage page)
{
    return static_cast<uint8_t>(page);
}

void advance_display_page()
{
    const uint8_t next = (display_page_index(s_device.display_page) + 1) % kDisplayPageCount;
    s_device.display_page = static_cast<DisplayPage>(next);
    ESP_LOGI(TAG, "OLED page: %s (%u/%u)",
             display_page_name(s_device.display_page),
             static_cast<unsigned>(next + 1),
             static_cast<unsigned>(kDisplayPageCount));
}

void log_onboarding_codes()
{
    char qr_code_buffer[256] = {};
    chip::MutableCharSpan qr_code(qr_code_buffer);
    CHIP_ERROR qr_err = GetQRCode(qr_code, chip::RendezvousInformationFlag::kBLE);
    if (qr_err == CHIP_NO_ERROR) {
        std::snprintf(s_device.onboarding_qr_code, sizeof(s_device.onboarding_qr_code), "%s", qr_code.data());
        s_device.onboarding_qr_ready = true;
        ESP_LOGI(TAG, "BLE onboarding QR code: %s", qr_code.data());
    } else {
        s_device.onboarding_qr_ready = false;
        s_device.onboarding_qr_code[0] = '\0';
        ESP_LOGW(TAG, "Failed to generate BLE onboarding QR code: %s", chip::ErrorStr(qr_err));
    }

    char manual_code_buffer[chip::kManualSetupLongCodeCharLength + 1] = {};
    chip::MutableCharSpan manual_code(manual_code_buffer);
    CHIP_ERROR manual_err = GetManualPairingCode(manual_code, chip::RendezvousInformationFlag::kBLE);
    if (manual_err == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "BLE manual pairing code: %s", manual_code.data());
    } else {
        ESP_LOGW(TAG, "Failed to generate BLE manual pairing code: %s", chip::ErrorStr(manual_err));
    }
}

void log_commissioning_state(const char *reason)
{
    const size_t fabric_count = chip::Server::GetInstance().GetFabricTable().FabricCount();
    s_device.fabric_count = fabric_count;
    s_indicator.commissioned = fabric_count > 0;
    ESP_LOGI(TAG, "Commissioning state (%s): fabric_count=%u commissioned=%s",
             reason,
             static_cast<unsigned>(fabric_count),
             fabric_count > 0 ? "yes" : "no");
}

const uint8_t *font5x7(char c)
{
    static const uint8_t blank[5] = {0x00, 0x00, 0x00, 0x00, 0x00};
    static const uint8_t glyph_0[5] = {0x3e, 0x51, 0x49, 0x45, 0x3e};
    static const uint8_t glyph_1[5] = {0x00, 0x42, 0x7f, 0x40, 0x00};
    static const uint8_t glyph_2[5] = {0x42, 0x61, 0x51, 0x49, 0x46};
    static const uint8_t glyph_3[5] = {0x21, 0x41, 0x45, 0x4b, 0x31};
    static const uint8_t glyph_4[5] = {0x18, 0x14, 0x12, 0x7f, 0x10};
    static const uint8_t glyph_5[5] = {0x27, 0x45, 0x45, 0x45, 0x39};
    static const uint8_t glyph_6[5] = {0x3c, 0x4a, 0x49, 0x49, 0x30};
    static const uint8_t glyph_7[5] = {0x01, 0x71, 0x09, 0x05, 0x03};
    static const uint8_t glyph_8[5] = {0x36, 0x49, 0x49, 0x49, 0x36};
    static const uint8_t glyph_9[5] = {0x06, 0x49, 0x49, 0x29, 0x1e};
    static const uint8_t glyph_a[5] = {0x7e, 0x11, 0x11, 0x11, 0x7e};
    static const uint8_t glyph_b[5] = {0x7f, 0x49, 0x49, 0x49, 0x36};
    static const uint8_t glyph_c[5] = {0x3e, 0x41, 0x41, 0x41, 0x22};
    static const uint8_t glyph_d[5] = {0x7f, 0x41, 0x41, 0x22, 0x1c};
    static const uint8_t glyph_e[5] = {0x7f, 0x49, 0x49, 0x49, 0x41};
    static const uint8_t glyph_f[5] = {0x7f, 0x09, 0x09, 0x09, 0x01};
    static const uint8_t glyph_g[5] = {0x3e, 0x41, 0x49, 0x49, 0x7a};
    static const uint8_t glyph_h[5] = {0x7f, 0x08, 0x08, 0x08, 0x7f};
    static const uint8_t glyph_i[5] = {0x00, 0x41, 0x7f, 0x41, 0x00};
    static const uint8_t glyph_j[5] = {0x20, 0x40, 0x41, 0x3f, 0x01};
    static const uint8_t glyph_k[5] = {0x7f, 0x08, 0x14, 0x22, 0x41};
    static const uint8_t glyph_l[5] = {0x7f, 0x40, 0x40, 0x40, 0x40};
    static const uint8_t glyph_m[5] = {0x7f, 0x02, 0x0c, 0x02, 0x7f};
    static const uint8_t glyph_n[5] = {0x7f, 0x04, 0x08, 0x10, 0x7f};
    static const uint8_t glyph_o[5] = {0x3e, 0x41, 0x41, 0x41, 0x3e};
    static const uint8_t glyph_p[5] = {0x7f, 0x09, 0x09, 0x09, 0x06};
    static const uint8_t glyph_q[5] = {0x3e, 0x41, 0x51, 0x21, 0x5e};
    static const uint8_t glyph_r[5] = {0x7f, 0x09, 0x19, 0x29, 0x46};
    static const uint8_t glyph_s[5] = {0x46, 0x49, 0x49, 0x49, 0x31};
    static const uint8_t glyph_t[5] = {0x01, 0x01, 0x7f, 0x01, 0x01};
    static const uint8_t glyph_u[5] = {0x3f, 0x40, 0x40, 0x40, 0x3f};
    static const uint8_t glyph_v[5] = {0x1f, 0x20, 0x40, 0x20, 0x1f};
    static const uint8_t glyph_w[5] = {0x3f, 0x40, 0x38, 0x40, 0x3f};
    static const uint8_t glyph_x[5] = {0x63, 0x14, 0x08, 0x14, 0x63};
    static const uint8_t glyph_y[5] = {0x07, 0x08, 0x70, 0x08, 0x07};
    static const uint8_t glyph_z[5] = {0x61, 0x51, 0x49, 0x45, 0x43};
    static const uint8_t colon[5] = {0x00, 0x36, 0x36, 0x00, 0x00};
    static const uint8_t dash[5] = {0x08, 0x08, 0x08, 0x08, 0x08};
    static const uint8_t dot[5] = {0x00, 0x60, 0x60, 0x00, 0x00};
    static const uint8_t slash[5] = {0x20, 0x10, 0x08, 0x04, 0x02};

    if (c >= 'a' && c <= 'z') {
        c = static_cast<char>(c - 'a' + 'A');
    }
    switch (c) {
    case '0': return glyph_0;
    case '1': return glyph_1;
    case '2': return glyph_2;
    case '3': return glyph_3;
    case '4': return glyph_4;
    case '5': return glyph_5;
    case '6': return glyph_6;
    case '7': return glyph_7;
    case '8': return glyph_8;
    case '9': return glyph_9;
    case 'A': return glyph_a;
    case 'B': return glyph_b;
    case 'C': return glyph_c;
    case 'D': return glyph_d;
    case 'E': return glyph_e;
    case 'F': return glyph_f;
    case 'G': return glyph_g;
    case 'H': return glyph_h;
    case 'I': return glyph_i;
    case 'J': return glyph_j;
    case 'K': return glyph_k;
    case 'L': return glyph_l;
    case 'M': return glyph_m;
    case 'N': return glyph_n;
    case 'O': return glyph_o;
    case 'P': return glyph_p;
    case 'Q': return glyph_q;
    case 'R': return glyph_r;
    case 'S': return glyph_s;
    case 'T': return glyph_t;
    case 'U': return glyph_u;
    case 'V': return glyph_v;
    case 'W': return glyph_w;
    case 'X': return glyph_x;
    case 'Y': return glyph_y;
    case 'Z': return glyph_z;
    case ':': return colon;
    case '-': return dash;
    case '.': return dot;
    case '/': return slash;
    default: return blank;
    }
}

esp_err_t oled_command(uint8_t addr, uint8_t command)
{
    const uint8_t payload[2] = {0x00, command};
    return i2c_master_write_to_device(kExpansionI2cPort, addr, payload, sizeof(payload), pdMS_TO_TICKS(100));
}

esp_err_t oled_command(uint8_t command)
{
    if (s_device.oled_addr == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    return oled_command(s_device.oled_addr, command);
}

esp_err_t i2c_read_reg(uint8_t addr, uint8_t reg, uint8_t *data, size_t len)
{
    return i2c_master_write_read_device(kExpansionI2cPort, addr, &reg, 1, data, len, pdMS_TO_TICKS(100));
}

esp_err_t i2c_write_reg(uint8_t addr, uint8_t reg, uint8_t value)
{
    const uint8_t payload[2] = {reg, value};
    return i2c_master_write_to_device(kExpansionI2cPort, addr, payload, sizeof(payload), pdMS_TO_TICKS(100));
}

esp_err_t i2c_write_regs(uint8_t addr, uint8_t reg, const uint8_t *data, size_t len)
{
    if (!data || len == 0 || len > 7) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t payload[8] = {reg};
    std::memcpy(payload + 1, data, len);
    return i2c_master_write_to_device(kExpansionI2cPort, addr, payload, len + 1, pdMS_TO_TICKS(100));
}

esp_err_t bma400_update_reg(uint8_t reg, uint8_t mask, uint8_t value)
{
    uint8_t current = 0;
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBma400Addr, reg, &current, sizeof(current)),
                        TAG, "Failed to read BMA400 register 0x%02x", reg);
    const uint8_t next = static_cast<uint8_t>((current & ~mask) | (value & mask));
    return i2c_write_reg(kBma400Addr, reg, next);
}

void oled_clear_buffer()
{
    std::memset(s_oled_buffer, 0, sizeof(s_oled_buffer));
}

bool take_oled_mutex(TickType_t timeout)
{
    if (!s_oled_mutex) {
        return true;
    }
    return xSemaphoreTake(s_oled_mutex, timeout) == pdTRUE;
}

void give_oled_mutex()
{
    if (s_oled_mutex) {
        xSemaphoreGive(s_oled_mutex);
    }
}

void oled_draw_text(int x, int page, const char *text)
{
    if (page < 0 || page >= kOledPages) {
        return;
    }
    while (*text && x < kOledWidth - 5) {
        const uint8_t *glyph = font5x7(*text++);
        for (int col = 0; col < 5; ++col) {
            s_oled_buffer[page * kOledWidth + x + col] = glyph[col];
        }
        x += 6;
    }
}

void oled_draw_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= kOledWidth || y < 0 || y >= kOledHeight) {
        return;
    }
    uint8_t &pixel_column = s_oled_buffer[(y / 8) * kOledWidth + x];
    const uint8_t mask = static_cast<uint8_t>(1U << (y % 8));
    if (on) {
        pixel_column |= mask;
    } else {
        pixel_column &= static_cast<uint8_t>(~mask);
    }
}

void oled_fill_rect(int x, int y, int width, int height, bool on)
{
    for (int py = y; py < y + height; ++py) {
        for (int px = x; px < x + width; ++px) {
            oled_draw_pixel(px, py, on);
        }
    }
}

bool oled_draw_onboarding_qr()
{
    if (!s_device.onboarding_qr_ready || s_device.onboarding_qr_code[0] == '\0') {
        return false;
    }

    uint8_t qr[qrcodegen_BUFFER_LEN_FOR_VERSION(kQrMaxVersion)] = {};
    uint8_t temp[qrcodegen_BUFFER_LEN_FOR_VERSION(kQrMaxVersion)] = {};
    if (!qrcodegen_encodeText(s_device.onboarding_qr_code, temp, qr, qrcodegen_Ecc_LOW,
                              1, kQrMaxVersion, qrcodegen_Mask_AUTO, true)) {
        ESP_LOGW(TAG, "Failed to encode OLED onboarding QR");
        return false;
    }

    const int qr_size = qrcodegen_getSize(qr);
    const int display_size = (qr_size + 2 * kQrBorder) * kQrScale;
    if (display_size > kOledWidth || display_size > kOledHeight) {
        ESP_LOGW(TAG, "OLED onboarding QR too large: qr=%d display=%d", qr_size, display_size);
        return false;
    }

    const int display_x = (kOledWidth - display_size) / 2;
    const int display_y = (kOledHeight - display_size) / 2;
    oled_clear_buffer();
    oled_fill_rect(display_x, display_y, display_size, display_size, true);
    for (int y = 0; y < qr_size; ++y) {
        for (int x = 0; x < qr_size; ++x) {
            if (qrcodegen_getModule(qr, x, y)) {
                oled_fill_rect(display_x + (x + kQrBorder) * kQrScale,
                               display_y + (y + kQrBorder) * kQrScale,
                               kQrScale, kQrScale, false);
            }
        }
    }
    return true;
}

void oled_draw_page_header(DisplayPage page)
{
    char line[24] = {};
    std::snprintf(line, sizeof(line), "%u/%u %s",
                  static_cast<unsigned>(display_page_index(page) + 1),
                  static_cast<unsigned>(kDisplayPageCount),
                  display_page_name(page));
    oled_draw_text(0, 0, line);
}

void oled_draw_i2c_found_line(int page)
{
    char line[24] = {};
    int used = std::snprintf(line, sizeof(line), "FOUND:");
    for (size_t index = 0; index < sizeof(kI2cProbeAddrs) && used > 0 && used < static_cast<int>(sizeof(line)) - 4; ++index) {
        if ((s_device.i2c_found_mask & (1U << index)) == 0) {
            continue;
        }
        used += std::snprintf(line + used, sizeof(line) - used, " %02X", kI2cProbeAddrs[index]);
    }
    oled_draw_text(0, page, line);
}

void oled_draw_status_page()
{
    char line[32] = {};
    const DisplayPage page = s_device.display_page;
    oled_clear_buffer();
    oled_draw_page_header(page);

    switch (page) {
    case DisplayPage::Overview:
        oled_draw_text(0, 1, "BMS XIAO C6");
        std::snprintf(line, sizeof(line), "NET: %s", s_indicator.thread_attached ? "ONLINE" : "DETACHED");
        oled_draw_text(0, 2, line);
        std::snprintf(line, sizeof(line), "PAIR: %s", s_indicator.window_open ? "OPEN" : (s_indicator.commissioned ? "YES" : "NO"));
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "FABRICS: %u", static_cast<unsigned>(s_device.fabric_count));
        oled_draw_text(0, 4, line);
        if (std::isfinite(s_device.last_temperature_c)) {
            std::snprintf(line, sizeof(line), "TEMP: %.1f C", static_cast<double>(s_device.last_temperature_c));
        } else {
            std::snprintf(line, sizeof(line), "TEMP: --.- C");
        }
        oled_draw_text(0, 5, line);
        oled_draw_text(0, 7, "CLICK: NEXT");
        break;

    case DisplayPage::Infrared:
        std::snprintf(line, sizeof(line), "STATE: %s", s_device.infrared_reflection ? "HIT" : "CLEAR");
        oled_draw_text(0, 2, line);
        std::snprintf(line, sizeof(line), "RAW: %d", gpio_get_level(kInfraredReflectiveGpio));
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "PIN: D0 GPIO%u", static_cast<unsigned>(kInfraredReflectiveGpio));
        oled_draw_text(0, 4, line);
        oled_draw_text(0, 6, "LOW: HIT");
        break;

    case DisplayPage::Accelerometer:
        std::snprintf(line, sizeof(line), "TYPE: %s", s_device.bma400_present ? "BMA400" : "--");
        oled_draw_text(0, 1, line);
        std::snprintf(line, sizeof(line), "ADDR: 0X%02X ID: 0X%02X", kBma400Addr, s_device.bma400_chip_id);
        oled_draw_text(0, 2, line);
        std::snprintf(line, sizeof(line), "KNOCK: %s", s_device.bma400_double_tap_ready ? "READY" : "--");
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "DOUBLE: %u", static_cast<unsigned>(s_device.bma400_double_tap_count));
        oled_draw_text(0, 4, line);
        std::snprintf(line, sizeof(line), "SIG:%3u PEAK:%3u",
                      static_cast<unsigned>(s_device.bma400_knock_signal),
                      static_cast<unsigned>(s_device.bma400_knock_peak));
        oled_draw_text(0, 5, line);
        std::snprintf(line, sizeof(line), "XYZ:%d %d %d",
                      static_cast<int>(s_device.bma400_x),
                      static_cast<int>(s_device.bma400_y),
                      static_cast<int>(s_device.bma400_z));
        oled_draw_text(0, 6, line);
        std::snprintf(line, sizeof(line), "INT:%04X ERR:%u",
                      s_device.bma400_last_interrupt_status,
                      static_cast<unsigned>(s_device.bma400_tap_read_error_count));
        oled_draw_text(0, 7, line);
        break;

    case DisplayPage::Temperature:
        oled_draw_text(0, 2, "SOURCE: ESP32C6");
        if (std::isfinite(s_device.last_temperature_c)) {
            std::snprintf(line, sizeof(line), "CHIP: %.1f C", static_cast<double>(s_device.last_temperature_c));
        } else {
            std::snprintf(line, sizeof(line), "CHIP: --.- C");
        }
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "PERIOD: %us", static_cast<unsigned>(kTelemetryPeriodMs / 1000));
        oled_draw_text(0, 4, line);
        break;

    case DisplayPage::I2c:
        std::snprintf(line, sizeof(line), "SDA: GPIO%u", static_cast<unsigned>(kExpansionI2cSdaGpio));
        oled_draw_text(0, 1, line);
        std::snprintf(line, sizeof(line), "SCL: GPIO%u", static_cast<unsigned>(kExpansionI2cSclGpio));
        oled_draw_text(0, 2, line);
        oled_draw_i2c_found_line(3);
        std::snprintf(line, sizeof(line), "TIMEOUT: %u", static_cast<unsigned>(s_device.i2c_timeout_count));
        oled_draw_text(0, 5, line);
        std::snprintf(line, sizeof(line), "FAIL: %u OTHER: %u",
                      static_cast<unsigned>(s_device.i2c_fail_count),
                      static_cast<unsigned>(s_device.i2c_other_error_count));
        oled_draw_text(0, 6, line);
        break;

    case DisplayPage::Matter:
        std::snprintf(line, sizeof(line), "THREAD: %s", s_indicator.thread_attached ? "ONLINE" : "DETACHED");
        oled_draw_text(0, 1, line);
        std::snprintf(line, sizeof(line), "COMM: %s", s_indicator.commissioned ? "YES" : "NO");
        oled_draw_text(0, 2, line);
        std::snprintf(line, sizeof(line), "WINDOW: %s", s_indicator.window_open ? "OPEN" : "CLOSED");
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "FABRICS: %u", static_cast<unsigned>(s_device.fabric_count));
        oled_draw_text(0, 4, line);
        std::snprintf(line, sizeof(line), "EP TEMP: %u", static_cast<unsigned>(s_device.temp_ep_id));
        oled_draw_text(0, 5, line);
        std::snprintf(line, sizeof(line), "EP BTN: %u", static_cast<unsigned>(s_device.button_ep_id));
        oled_draw_text(0, 6, line);
        break;

    case DisplayPage::System: {
        const uint32_t uptime_s = (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000;
        std::snprintf(line, sizeof(line), "FW: %.12s", kBoardIdentity.software_version_str);
        oled_draw_text(0, 1, line);
        std::snprintf(line, sizeof(line), "UP: %us", static_cast<unsigned>(uptime_s));
        oled_draw_text(0, 2, line);
        std::snprintf(line, sizeof(line), "HEAP: %u", static_cast<unsigned>(esp_get_free_heap_size()));
        oled_draw_text(0, 3, line);
        std::snprintf(line, sizeof(line), "RESET: %s", reset_reason_str(s_device.reset_reason));
        oled_draw_text(0, 4, line);
        std::snprintf(line, sizeof(line), "USER: %s", s_device.button_pressed ? "PRESSED" : "READY");
        oled_draw_text(0, 6, line);
        break;
    }
    }
}

esp_err_t oled_refresh()
{
    if (!s_device.oled_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    for (uint8_t page = 0; page < kOledPages; ++page) {
        ESP_RETURN_ON_ERROR(oled_command(static_cast<uint8_t>(0xb0 | page)), TAG, "Failed to select OLED page");
        ESP_RETURN_ON_ERROR(oled_command(0x00), TAG, "Failed to set OLED low column");
        ESP_RETURN_ON_ERROR(oled_command(0x10), TAG, "Failed to set OLED high column");

        uint8_t payload[kOledWidth + 1] = {0x40};
        std::memcpy(payload + 1, s_oled_buffer + page * kOledWidth, kOledWidth);
        ESP_RETURN_ON_ERROR(i2c_master_write_to_device(kExpansionI2cPort, s_device.oled_addr, payload, sizeof(payload), pdMS_TO_TICKS(100)),
                            TAG, "Failed to write OLED data");
    }
    return ESP_OK;
}

void oled_render_status()
{
    if (!s_device.oled_ready) {
        return;
    }
    if (!take_oled_mutex(pdMS_TO_TICKS(250))) {
        ESP_LOGW(TAG, "Skipping OLED refresh: display busy");
        return;
    }

    if (!(s_indicator.window_open && oled_draw_onboarding_qr())) {
        oled_draw_status_page();
    }

    esp_err_t err = oled_refresh();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to refresh OLED: %s", esp_err_to_name(err));
    }
    give_oled_mutex();
}

esp_err_t init_expansion_i2c()
{
    gpio_config_t recovery_config = {};
    recovery_config.pin_bit_mask = (1ULL << kExpansionI2cSdaGpio) | (1ULL << kExpansionI2cSclGpio);
    recovery_config.mode = GPIO_MODE_INPUT_OUTPUT_OD;
    recovery_config.pull_up_en = GPIO_PULLUP_ENABLE;
    recovery_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    recovery_config.intr_type = GPIO_INTR_DISABLE;
    ESP_RETURN_ON_ERROR(gpio_config(&recovery_config), TAG, "Failed to configure I2C recovery GPIOs");

    gpio_set_level(kExpansionI2cSdaGpio, 1);
    gpio_set_level(kExpansionI2cSclGpio, 1);
    esp_rom_delay_us(20);
    ESP_LOGI(TAG, "I2C line levels before recovery: SDA=%d SCL=%d",
             gpio_get_level(kExpansionI2cSdaGpio),
             gpio_get_level(kExpansionI2cSclGpio));

    if (gpio_get_level(kExpansionI2cSdaGpio) == 0 || gpio_get_level(kExpansionI2cSclGpio) == 0) {
        ESP_LOGW(TAG, "I2C bus looks busy/stuck before driver install; pulsing SCL");
        for (int i = 0; i < 9; ++i) {
            gpio_set_level(kExpansionI2cSclGpio, 0);
            esp_rom_delay_us(5);
            gpio_set_level(kExpansionI2cSclGpio, 1);
            esp_rom_delay_us(5);
        }
        gpio_set_level(kExpansionI2cSdaGpio, 0);
        esp_rom_delay_us(5);
        gpio_set_level(kExpansionI2cSclGpio, 1);
        esp_rom_delay_us(5);
        gpio_set_level(kExpansionI2cSdaGpio, 1);
        esp_rom_delay_us(20);
    }

    ESP_LOGI(TAG, "I2C line levels after recovery: SDA=%d SCL=%d",
             gpio_get_level(kExpansionI2cSdaGpio),
             gpio_get_level(kExpansionI2cSclGpio));

    i2c_config_t i2c_config = {};
    i2c_config.mode = I2C_MODE_MASTER;
    i2c_config.sda_io_num = kExpansionI2cSdaGpio;
    i2c_config.scl_io_num = kExpansionI2cSclGpio;
    i2c_config.sda_pullup_en = GPIO_PULLUP_ENABLE;
    i2c_config.scl_pullup_en = GPIO_PULLUP_ENABLE;
    i2c_config.master.clk_speed = kExpansionI2cClockHz;
    ESP_RETURN_ON_ERROR(i2c_param_config(kExpansionI2cPort, &i2c_config), TAG, "Failed to configure expansion I2C");
    return i2c_driver_install(kExpansionI2cPort, I2C_MODE_MASTER, 0, 0, 0);
}

esp_err_t i2c_probe(uint8_t addr)
{
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    if (!cmd) {
        return ESP_ERR_NO_MEM;
    }
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, static_cast<uint8_t>((addr << 1) | I2C_MASTER_WRITE), true);
    i2c_master_stop(cmd);
    const esp_err_t ret = i2c_master_cmd_begin(kExpansionI2cPort, cmd, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(cmd);
    return ret;
}

void log_i2c_scan()
{
    bool found_any = false;
    uint16_t found_mask = 0;
    uint32_t timeout_count = 0;
    uint32_t fail_count = 0;
    uint32_t other_error_count = 0;
    for (size_t index = 0; index < sizeof(kI2cProbeAddrs); ++index) {
        const uint8_t addr = kI2cProbeAddrs[index];
        const esp_err_t ret = i2c_probe(addr);
        if (ret == ESP_OK) {
            ESP_LOGI(TAG, "I2C device detected at 0x%02x", addr);
            found_any = true;
            found_mask |= static_cast<uint16_t>(1U << index);
        } else if (ret == ESP_ERR_TIMEOUT) {
            ++timeout_count;
        } else if (ret == ESP_FAIL) {
            ++fail_count;
        } else {
            ++other_error_count;
        }
    }
    s_device.i2c_found_mask = found_mask;
    s_device.i2c_timeout_count = timeout_count;
    s_device.i2c_fail_count = fail_count;
    s_device.i2c_other_error_count = other_error_count;
    if (!found_any) {
        ESP_LOGW(TAG, "I2C probe found no known devices on SDA=GPIO%u SCL=GPIO%u (timeout=%u fail=%u other=%u)",
                 static_cast<unsigned>(kExpansionI2cSdaGpio),
                 static_cast<unsigned>(kExpansionI2cSclGpio),
                 static_cast<unsigned>(timeout_count),
                 static_cast<unsigned>(fail_count),
                 static_cast<unsigned>(other_error_count));
    } else {
        ESP_LOGI(TAG, "I2C known-address probe complete (timeout=%u fail=%u other=%u)",
                 static_cast<unsigned>(timeout_count),
                 static_cast<unsigned>(fail_count),
                 static_cast<unsigned>(other_error_count));
    }
}

esp_err_t probe_bma400()
{
    uint8_t chip_id = 0;
    const esp_err_t ret = i2c_read_reg(kBma400Addr, kBma400ChipIdReg, &chip_id, sizeof(chip_id));
    if (ret != ESP_OK) {
        s_device.bma400_present = false;
        s_device.bma400_chip_id = 0;
        ESP_LOGW(TAG, "BMA400 probe failed at 0x%02x: %s", kBma400Addr, esp_err_to_name(ret));
        return ret;
    }

    s_device.bma400_chip_id = chip_id;
    s_device.bma400_present = chip_id == kBma400ExpectedChipId;
    if (s_device.bma400_present) {
        ESP_LOGI(TAG, "BMA400 detected at 0x%02x: CHIPID=0x%02x", kBma400Addr, chip_id);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "Unexpected device at 0x%02x: CHIPID=0x%02x (expected BMA400 0x%02x)",
             kBma400Addr, chip_id, kBma400ExpectedChipId);
    return ESP_ERR_INVALID_RESPONSE;
}

esp_err_t read_bma400_interrupt_status(uint16_t *status)
{
    if (!status) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t data[3] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBma400Addr, kBma400RegIntStat0, data, sizeof(data)),
                        TAG, "Failed to read BMA400 interrupt status");
    data[1] = static_cast<uint8_t>((data[1] & ~kBma400IntStatusMask) |
                                   ((data[2] << kBma400IntStatusPos) & kBma400IntStatusMask));
    *status = static_cast<uint16_t>((static_cast<uint16_t>(data[1]) << 8) | data[0]);
    return ESP_OK;
}

int16_t decode_bma400_axis(uint8_t lsb, uint8_t msb)
{
    uint16_t raw = static_cast<uint16_t>((static_cast<uint16_t>(msb) << 8) | lsb) & 0x0fff;
    if (raw & 0x0800) {
        raw |= 0xf000;
    }
    return static_cast<int16_t>(raw);
}

esp_err_t read_bma400_accel(int16_t *x, int16_t *y, int16_t *z)
{
    if (!x || !y || !z) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t data[6] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBma400Addr, kBma400RegAccelData, data, sizeof(data)),
                        TAG, "Failed to read BMA400 accel data");
    *x = decode_bma400_axis(data[0], data[1]);
    *y = decode_bma400_axis(data[2], data[3]);
    *z = decode_bma400_axis(data[4], data[5]);
    return ESP_OK;
}

esp_err_t init_bma400_double_tap()
{
    s_device.bma400_double_tap_ready = false;
    if (!s_device.bma400_present) {
        ESP_LOGW(TAG, "BMA400 double tap detector skipped: accelerometer is missing");
        return ESP_ERR_NOT_FOUND;
    }

    uint8_t accel_config[3] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBma400Addr, kBma400RegAccelConfig0, accel_config, sizeof(accel_config)),
                        TAG, "Failed to read BMA400 accel config");
    accel_config[0] = static_cast<uint8_t>((accel_config[0] & ~(kBma400PowerModeMask | kBma400Filt1BwMask)) |
                                           kBma400PowerModeNormal |
                                           (kBma400Filt1Bw1 << kBma400Filt1BwPos));
    accel_config[1] = static_cast<uint8_t>((accel_config[1] & ~(kBma400AccelOdrMask | kBma400AccelRangeMask)) |
                                           kBma400AccelOdr400Hz |
                                           (kBma400AccelRange16g << kBma400AccelRangePos));
    accel_config[2] = static_cast<uint8_t>(accel_config[2] & ~kBma400DataFilterMask);
    ESP_RETURN_ON_ERROR(i2c_write_regs(kBma400Addr, kBma400RegAccelConfig0, accel_config, sizeof(accel_config)),
                        TAG, "Failed to write BMA400 accel config");

    uint8_t tap_config[2] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBma400Addr, kBma400RegTapConfig, tap_config, sizeof(tap_config)),
                        TAG, "Failed to read BMA400 tap config");
    tap_config[0] = static_cast<uint8_t>((tap_config[0] & ~(kBma400TapAxesMask | kBma400TapSensitivityMask)) |
                                         (kBma400TapAxesXyz << kBma400TapAxesPos));
    tap_config[1] = static_cast<uint8_t>(tap_config[1] & ~kBma400TapTimingMask);
    ESP_RETURN_ON_ERROR(i2c_write_regs(kBma400Addr, kBma400RegTapConfig, tap_config, sizeof(tap_config)),
                        TAG, "Failed to write BMA400 tap config");

    ESP_RETURN_ON_ERROR(bma400_update_reg(kBma400RegIntConf1,
                                          kBma400SingleTapEnableMask | kBma400DoubleTapEnableMask,
                                          kBma400SingleTapEnableMask | kBma400DoubleTapEnableMask),
                        TAG, "Failed to enable BMA400 tap interrupts");
    ESP_RETURN_ON_ERROR(bma400_update_reg(kBma400RegIntMap, kBma400TapMapInt1Mask, kBma400TapMapInt1Mask),
                        TAG, "Failed to map BMA400 tap interrupt to INT1");

    uint16_t status = 0;
    if (read_bma400_interrupt_status(&status) == ESP_OK) {
        s_device.bma400_last_interrupt_status = status;
    }

    s_device.bma400_double_tap_ready = true;
    ESP_LOGI(TAG, "BMA400 knock detector enabled: odr=400Hz range=16g raw_threshold=%.1f int_sensitivity=0",
             static_cast<double>(kBma400KnockThreshold));
    return ESP_OK;
}

esp_err_t try_init_oled_at(uint8_t addr)
{
    const uint8_t commands[] = {
        0xae, 0xd5, 0x80, 0xa8, 0x3f, 0xd3, 0x00, 0x40,
        0x8d, 0x14, 0x20, 0x00, 0xa1, 0xc8, 0xda, 0x12,
        0x81, 0xcf, 0xd9, 0xf1, 0xdb, 0x40, 0xa4, 0xa6,
        0x2e, 0xaf,
    };
    for (uint8_t command : commands) {
        const esp_err_t ret = oled_command(addr, command);
        if (ret != ESP_OK) {
            return ret;
        }
    }
    s_device.oled_addr = addr;
    s_device.oled_ready = true;
    ESP_LOGI(TAG, "OLED ready at I2C address 0x%02x", addr);
    oled_render_status();
    return ESP_OK;
}

esp_err_t init_oled()
{
    s_device.oled_ready = false;
    s_device.oled_addr = 0;
    log_i2c_scan();

    esp_err_t last_error = ESP_ERR_NOT_FOUND;
    for (uint8_t addr : kOledCandidateAddrs) {
        ESP_LOGI(TAG, "Trying SSD1306 OLED at I2C address 0x%02x", addr);
        const esp_err_t ret = try_init_oled_at(addr);
        if (ret == ESP_OK) {
            return ESP_OK;
        }
        ESP_LOGW(TAG, "OLED did not answer at 0x%02x: %s", addr, esp_err_to_name(ret));
        last_error = ret;
    }
    return last_error;
}

esp_err_t set_led_brightness(uint8_t brightness)
{
    // The XIAO ESP32-C6 user LED is active-low on GPIO15.
    const uint32_t capped = (static_cast<uint32_t>(brightness) * kLedBrightnessCap) / 255;
    const uint32_t duty = kStatusLedMaxDuty - capped;
    ESP_RETURN_ON_ERROR(ledc_set_duty(kStatusLedSpeedMode, kStatusLedChannel, duty),
                        TAG, "Failed to set status LED duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(kStatusLedSpeedMode, kStatusLedChannel),
                        TAG, "Failed to update status LED duty");
    return ESP_OK;
}

uint8_t breath_intensity(uint32_t tick_ms, uint32_t period_ms)
{
    const float phase = static_cast<float>(tick_ms % period_ms) / static_cast<float>(period_ms);
    const float v = 0.5f - 0.5f * std::cos(phase * 2.0f * static_cast<float>(M_PI));
    return static_cast<uint8_t>(v * 255.0f);
}

bool blink_on(uint32_t tick_ms, uint32_t half_period_ms)
{
    return (tick_ms / half_period_ms) % 2 == 0;
}

IndicatorState compute_indicator_state(uint32_t since_boot_ms)
{
    if (s_indicator.factory_reset_active)         return IndicatorState::FactoryResetActive;
    if (s_indicator.button_preview_factory_reset) return IndicatorState::FactoryResetPreview;
    if (s_indicator.button_preview_commissioning) return IndicatorState::CommissioningPreview;
    if (since_boot_ms < kBootIndicatorMs)         return IndicatorState::Boot;
    if (s_indicator.window_open)                  return IndicatorState::Commissioning;
    if (!s_indicator.commissioned)                return IndicatorState::Commissioning;
    if (!s_indicator.thread_attached)             return IndicatorState::ThreadDetached;
    return IndicatorState::Running;
}

void render_indicator(IndicatorState state, uint32_t tick_ms)
{
    switch (state) {
    case IndicatorState::Boot: {
        const uint8_t i = breath_intensity(tick_ms, 1500);
        set_led_brightness(i);
        break;
    }
    case IndicatorState::Commissioning: {
        const bool on = blink_on(tick_ms, 250);
        set_led_brightness(on ? 180 : 0);
        break;
    }
    case IndicatorState::ThreadDetached: {
        const bool on = blink_on(tick_ms, 1000);
        set_led_brightness(on ? 140 : 20);
        break;
    }
    case IndicatorState::Running:
        set_led_brightness(40);
        break;
    case IndicatorState::CommissioningPreview: {
        const bool on = blink_on(tick_ms, 125);
        set_led_brightness(on ? 255 : 0);
        break;
    }
    case IndicatorState::FactoryResetPreview: {
        const bool on = blink_on(tick_ms, 80);
        set_led_brightness(on ? 255 : 0);
        break;
    }
    case IndicatorState::FactoryResetActive: {
        const bool on = blink_on(tick_ms, 100);
        set_led_brightness(on ? 255 : 0);
        break;
    }
    }
}

esp_err_t update_temperature_measurement(float temperature_c)
{
    esp_matter_attr_val_t temp_val = esp_matter_nullable_int16(
        nullable<int16_t>(to_matter_temp(temperature_c)));
    return update(s_device.temp_ep_id,
                  chip::app::Clusters::TemperatureMeasurement::Id,
                  chip::app::Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Id,
                  &temp_val);
}

esp_err_t update_button_position(bool pressed)
{
    esp_matter_attr_val_t position_val = esp_matter_uint8(pressed ? 1 : 0);
    return update(s_device.button_ep_id,
                  chip::app::Clusters::Switch::Id,
                  chip::app::Clusters::Switch::Attributes::CurrentPosition::Id,
                  &position_val);
}

esp_err_t publish_button_event(bool pressed)
{
    if (pressed) {
        return esp_matter::cluster::switch_cluster::event::send_initial_press(s_device.button_ep_id, 1);
    }
    return esp_matter::cluster::switch_cluster::event::send_short_release(s_device.button_ep_id, 1);
}

esp_err_t init_internal_temperature_sensor()
{
    temperature_sensor_config_t temp_sensor_config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 80);
    ESP_RETURN_ON_ERROR(temperature_sensor_install(&temp_sensor_config, &s_device.temp_handle), TAG, "Failed to install temperature sensor");
    ESP_RETURN_ON_ERROR(temperature_sensor_enable(s_device.temp_handle), TAG, "Failed to enable temperature sensor");
    return ESP_OK;
}

esp_err_t init_status_led()
{
    ledc_timer_config_t timer_config = {};
    timer_config.speed_mode = kStatusLedSpeedMode;
    timer_config.duty_resolution = kStatusLedResolution;
    timer_config.timer_num = kStatusLedTimer;
    timer_config.freq_hz = 5000;
    timer_config.clk_cfg = LEDC_AUTO_CLK;
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_config), TAG, "Failed to configure status LED timer");

    ledc_channel_config_t channel_config = {};
    channel_config.gpio_num = kStatusLedGpio;
    channel_config.speed_mode = kStatusLedSpeedMode;
    channel_config.channel = kStatusLedChannel;
    channel_config.intr_type = LEDC_INTR_DISABLE;
    channel_config.timer_sel = kStatusLedTimer;
    channel_config.duty = kStatusLedMaxDuty;
    channel_config.hpoint = 0;
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel_config), TAG, "Failed to configure status LED channel");

    ESP_RETURN_ON_ERROR(set_led_brightness(0), TAG, "Failed to turn status LED off");
    return ESP_OK;
}

esp_err_t set_buzzer_on(bool on)
{
    const uint32_t duty = on ? kBuzzerDuty : 0;
    ESP_RETURN_ON_ERROR(ledc_set_duty(kStatusLedSpeedMode, kBuzzerChannel, duty),
                        TAG, "Failed to set buzzer duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(kStatusLedSpeedMode, kBuzzerChannel),
                        TAG, "Failed to update buzzer duty");
    return ESP_OK;
}

esp_err_t init_buzzer()
{
    ledc_timer_config_t timer_config = {};
    timer_config.speed_mode = kStatusLedSpeedMode;
    timer_config.duty_resolution = kBuzzerResolution;
    timer_config.timer_num = kBuzzerTimer;
    timer_config.freq_hz = kBuzzerToneHz;
    timer_config.clk_cfg = LEDC_AUTO_CLK;
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_config), TAG, "Failed to configure buzzer timer");

    ledc_channel_config_t channel_config = {};
    channel_config.gpio_num = kExpansionBuzzerGpio;
    channel_config.speed_mode = kStatusLedSpeedMode;
    channel_config.channel = kBuzzerChannel;
    channel_config.intr_type = LEDC_INTR_DISABLE;
    channel_config.timer_sel = kBuzzerTimer;
    channel_config.duty = 0;
    channel_config.hpoint = 0;
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel_config), TAG, "Failed to configure buzzer channel");

    return set_buzzer_on(false);
}

void play_button_beep()
{
    if (set_buzzer_on(true) != ESP_OK) {
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(kButtonBeepMs));
    if (set_buzzer_on(false) != ESP_OK) {
        ESP_LOGW(TAG, "Failed to stop buzzer");
    }
}

esp_err_t init_boot_button()
{
    gpio_config_t boot_button_config = {};
    boot_button_config.pin_bit_mask = 1ULL << kUserButtonGpio;
    boot_button_config.mode = GPIO_MODE_INPUT;
    boot_button_config.pull_up_en = GPIO_PULLUP_ENABLE;
    boot_button_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    boot_button_config.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&boot_button_config);
}

esp_err_t init_infrared_reflective_sensor()
{
    gpio_config_t sensor_config = {};
    sensor_config.pin_bit_mask = 1ULL << kInfraredReflectiveGpio;
    sensor_config.mode = GPIO_MODE_INPUT;
    sensor_config.pull_up_en = GPIO_PULLUP_ENABLE;
    sensor_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    sensor_config.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&sensor_config);
}

void update_thread_state_cache()
{
    s_indicator.thread_attached = chip::DeviceLayer::ConnectivityMgr().IsThreadAttached();
    s_device.fabric_count = chip::Server::GetInstance().GetFabricTable().FabricCount();
    s_indicator.commissioned = s_device.fabric_count > 0;
    s_indicator.window_open = chip::Server::GetInstance().GetCommissioningWindowManager().IsCommissioningWindowOpen();
}

void schedule_commissioning_window()
{
    chip::DeviceLayer::PlatformMgr().ScheduleWork(
        [](intptr_t) {
            CHIP_ERROR err = chip::Server::GetInstance().GetCommissioningWindowManager()
                .OpenBasicCommissioningWindow(
                    chip::System::Clock::Seconds32(kCommissioningWindowSeconds),
                    chip::CommissioningWindowAdvertisement::kAllSupported);
            if (err != CHIP_NO_ERROR) {
                ESP_LOGE(TAG, "OpenBasicCommissioningWindow failed: %s", chip::ErrorStr(err));
            } else {
                ESP_LOGI(TAG, "Basic commissioning window opened (%us)",
                         static_cast<unsigned>(kCommissioningWindowSeconds));
            }
        }, 0);
}

void schedule_factory_reset()
{
    s_indicator.factory_reset_active = true;
    chip::Server::GetInstance().ScheduleFactoryReset();
}

esp_err_t init_test_event_trigger_delegate()
{
    CHIP_ERROR err = s_test_event_trigger_delegate.Init(chip::ByteSpan(s_test_event_enable_key));
    if (err != CHIP_NO_ERROR) {
        ESP_LOGE(TAG, "Failed to initialize TestEventTrigger delegate: %s", chip::ErrorStr(err));
        return ESP_FAIL;
    }

    err = s_test_event_trigger_delegate.AddHandler(&s_bms_test_event_trigger_handler);
    if (err != CHIP_NO_ERROR) {
        ESP_LOGE(TAG, "Failed to register BMS TestEventTrigger handler: %s", chip::ErrorStr(err));
        return ESP_FAIL;
    }

    esp_err_t ret = esp_matter::test_event_trigger::set_delegate(&s_test_event_trigger_delegate);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Matter air reboot trigger registered: 0x%llx",
                 static_cast<unsigned long long>(kBmsAirRebootEventTrigger));
    }
    return ret;
}



void indicator_task(void *)
{
    uint32_t tick_ms = 0;
    IndicatorState last = IndicatorState::Boot;
    while (true) {
        const uint32_t since_boot_ms =
            (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS;
        const IndicatorState state = compute_indicator_state(since_boot_ms);
        if (state != last) {
            ESP_LOGI(TAG, "Indicator: %s", indicator_state_str(state));
            last = state;
        }
        render_indicator(state, tick_ms);
        vTaskDelay(pdMS_TO_TICKS(kIndicatorPeriodMs));
        tick_ms += kIndicatorPeriodMs;
    }
}

void infrared_reflective_task(void *)
{
    bool last_sample = read_infrared_reflection_detected();
    bool reported_state = last_sample;
    TickType_t last_change_tick = xTaskGetTickCount();

    s_device.infrared_reflection = reported_state;
    ESP_LOGI(TAG, "IR reflective sensor initial state: %s",
             reported_state ? "detected" : "clear");
    oled_render_status();

    while (true) {
        const bool current_sample = read_infrared_reflection_detected();
        const TickType_t now = xTaskGetTickCount();

        if (current_sample != last_sample) {
            last_sample = current_sample;
            last_change_tick = now;
        } else if (current_sample != reported_state &&
                   (now - last_change_tick) >= pdMS_TO_TICKS(kInfraredDebounceMs)) {
            reported_state = current_sample;
            s_device.infrared_reflection = reported_state;
            ESP_LOGI(TAG, "IR reflective sensor %s",
                     reported_state ? "detected" : "clear");
            oled_render_status();
        }

        vTaskDelay(pdMS_TO_TICKS(kInfraredPollPeriodMs));
    }
}

void bma400_double_tap_task(void *)
{
    if (!s_device.bma400_double_tap_ready) {
        vTaskDelete(nullptr);
        return;
    }

    bool baseline_ready = false;
    bool above_threshold = false;
    bool first_knock_pending = false;
    float baseline_x = 0.0f;
    float baseline_y = 0.0f;
    float baseline_z = 0.0f;
    uint32_t first_knock_ms = 0;
    uint32_t cooldown_until_ms = 0;

    while (true) {
        uint16_t status = 0;
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        if (!take_oled_mutex(pdMS_TO_TICKS(20))) {
            vTaskDelay(pdMS_TO_TICKS(kBma400TapPollPeriodMs));
            continue;
        }

        esp_err_t err = read_bma400_interrupt_status(&status);
        if (err == ESP_OK) {
            err = read_bma400_accel(&x, &y, &z);
        }
        give_oled_mutex();

        if (err == ESP_OK) {
            s_device.bma400_last_interrupt_status = status;
            const uint32_t now_ms = (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS;
            s_device.bma400_x = x;
            s_device.bma400_y = y;
            s_device.bma400_z = z;

            if (!baseline_ready) {
                baseline_x = static_cast<float>(x);
                baseline_y = static_cast<float>(y);
                baseline_z = static_cast<float>(z);
                baseline_ready = true;
            }

            const float dx = std::fabs(static_cast<float>(x) - baseline_x);
            const float dy = std::fabs(static_cast<float>(y) - baseline_y);
            const float dz = std::fabs(static_cast<float>(z) - baseline_z);
            const float signal = dx + dy + dz;
            s_device.bma400_knock_signal = signal;
            if (signal > s_device.bma400_knock_peak) {
                s_device.bma400_knock_peak = signal;
            }

            baseline_x += (static_cast<float>(x) - baseline_x) * kBma400KnockBaselineAlpha;
            baseline_y += (static_cast<float>(y) - baseline_y) * kBma400KnockBaselineAlpha;
            baseline_z += (static_cast<float>(z) - baseline_z) * kBma400KnockBaselineAlpha;

            const bool internal_double_tap =
                (status & kBma400DoubleTapAsserted) &&
                now_ms >= cooldown_until_ms;
            const bool raw_knock_rising_edge =
                signal >= kBma400KnockThreshold &&
                !above_threshold &&
                now_ms >= cooldown_until_ms;

            if (signal < kBma400KnockReleaseThreshold) {
                above_threshold = false;
            } else if (signal >= kBma400KnockThreshold) {
                above_threshold = true;
            }

            bool double_knock_detected = false;
            if (raw_knock_rising_edge) {
                if (first_knock_pending) {
                    const uint32_t gap_ms = now_ms - first_knock_ms;
                    if (gap_ms >= kBma400DoubleKnockMinGapMs && gap_ms <= kBma400DoubleKnockWindowMs) {
                        double_knock_detected = true;
                        first_knock_pending = false;
                    } else {
                        first_knock_ms = now_ms;
                    }
                } else {
                    first_knock_pending = true;
                    first_knock_ms = now_ms;
                }
                ESP_LOGI(TAG, "BMA400 knock impulse: signal=%.1f peak=%.1f xyz=%d,%d,%d pending=%d",
                         static_cast<double>(signal),
                         static_cast<double>(s_device.bma400_knock_peak),
                         static_cast<int>(x),
                         static_cast<int>(y),
                         static_cast<int>(z),
                         static_cast<int>(first_knock_pending));
                oled_render_status();
            } else if (first_knock_pending && now_ms - first_knock_ms > kBma400DoubleKnockWindowMs) {
                first_knock_pending = false;
            }

            if (internal_double_tap || double_knock_detected) {
                ++s_device.bma400_double_tap_count;
                s_device.bma400_last_double_tap_ms = now_ms;
                cooldown_until_ms = now_ms + kBma400DoubleTapCooldownMs;
                ESP_LOGI(TAG, "BMA400 double knock detected: count=%u status=0x%04x signal=%.1f source=%s",
                         static_cast<unsigned>(s_device.bma400_double_tap_count),
                         status,
                         static_cast<double>(signal),
                         internal_double_tap ? "bma400-int" : "raw");
                oled_render_status();
            }
        } else {
            ++s_device.bma400_tap_read_error_count;
            if ((s_device.bma400_tap_read_error_count % 100) == 1) {
                ESP_LOGW(TAG, "BMA400 double tap status read failed: %s (errors=%u)",
                         esp_err_to_name(err),
                         static_cast<unsigned>(s_device.bma400_tap_read_error_count));
            }
        }

        vTaskDelay(pdMS_TO_TICKS(kBma400TapPollPeriodMs));
    }
}

void telemetry_task(void *)
{
    uint32_t since_heartbeat_ms = 0;
    while (true) {
        float temperature_c = 0.0f;
        esp_err_t err = temperature_sensor_get_celsius(s_device.temp_handle, &temperature_c);
        if (err == ESP_OK) {
            s_device.last_temperature_c = temperature_c;
            ESP_LOGI(TAG, "Updating chip temperature: %.2fC", temperature_c);
            err = update_temperature_measurement(temperature_c);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update temperature attribute: %s", esp_err_to_name(err));
            }
        } else {
                ESP_LOGW(TAG, "Failed to read chip temperature: %s", esp_err_to_name(err));
        }
        oled_render_status();

        since_heartbeat_ms += kTelemetryPeriodMs;
        if (since_heartbeat_ms >= kHeartbeatPeriodMs) {
            since_heartbeat_ms = 0;
            const uint32_t uptime_s =
                (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000;
            ESP_LOGI(TAG,
                     "Heartbeat: thread_attached=%d commissioned=%d window_open=%d "
                     "free_heap=%u uptime_s=%u",
                     static_cast<int>(s_indicator.thread_attached),
                     static_cast<int>(s_indicator.commissioned),
                     static_cast<int>(s_indicator.window_open),
                     static_cast<unsigned>(esp_get_free_heap_size()),
                     static_cast<unsigned>(uptime_s));
        }
        vTaskDelay(pdMS_TO_TICKS(kTelemetryPeriodMs));
    }
}

void button_task(void *)
{
    bool last_sample = read_boot_button_pressed();
    bool reported_state = last_sample;
    TickType_t last_change_tick = xTaskGetTickCount();
    TickType_t press_start_tick = last_sample ? xTaskGetTickCount() : 0;
    bool preview_commissioning_set = false;
    bool preview_factory_reset_set = false;

    s_device.button_pressed = reported_state;
    if (update_button_position(reported_state) != ESP_OK) {
        ESP_LOGW(TAG, "Failed to publish initial button position");
    }

    while (true) {
        const bool current_sample = read_boot_button_pressed();
        const TickType_t now = xTaskGetTickCount();

        if (current_sample != last_sample) {
            last_sample = current_sample;
            last_change_tick = now;
        } else if (current_sample != reported_state &&
                   (now - last_change_tick) >= pdMS_TO_TICKS(kButtonDebounceMs)) {
            reported_state = current_sample;
            s_device.button_pressed = reported_state;
            ESP_LOGI(TAG, "USER button %s", reported_state ? "pressed" : "released");
            if (reported_state) {
                play_button_beep();
            }
            oled_render_status();
            esp_err_t err = update_button_position(reported_state);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update button position: %s", esp_err_to_name(err));
            }
            err = publish_button_event(reported_state);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to publish button event: %s", esp_err_to_name(err));
            }
            if (reported_state) {
                press_start_tick = now;
                preview_commissioning_set = false;
                preview_factory_reset_set = false;
            } else {
                const uint32_t held_ms = (now - press_start_tick) * portTICK_PERIOD_MS;
                s_indicator.button_preview_commissioning = false;
                s_indicator.button_preview_factory_reset = false;
                if (held_ms >= kFactoryResetHoldMs) {
                    ESP_LOGW(TAG, "Factory reset requested (USER held %ums)",
                             static_cast<unsigned>(held_ms));
                    schedule_factory_reset();
                } else if (held_ms >= kCommissioningHoldMs) {
                    ESP_LOGI(TAG, "Commissioning window requested (USER held %ums)",
                             static_cast<unsigned>(held_ms));
                    schedule_commissioning_window();
                } else if (held_ms <= kShortPressMaxMs) {
                    advance_display_page();
                    oled_render_status();
                }
            }
        }

        if (reported_state) {
            const uint32_t held_ms = (now - press_start_tick) * portTICK_PERIOD_MS;
            if (held_ms >= kFactoryResetHoldMs && !preview_factory_reset_set) {
                preview_factory_reset_set = true;
                s_indicator.button_preview_commissioning = false;
                s_indicator.button_preview_factory_reset = true;
            } else if (held_ms >= kCommissioningHoldMs && !preview_commissioning_set) {
                preview_commissioning_set = true;
                s_indicator.button_preview_commissioning = true;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(kButtonPollPeriodMs));
    }
}

esp_err_t attr_callback(attribute::callback_type_t type, uint16_t endpoint_id,
                        uint32_t cluster_id, uint32_t attribute_id,
                        esp_matter_attr_val_t *val, void *priv_data)
{
    (void)type;
    (void)endpoint_id;
    (void)cluster_id;
    (void)attribute_id;
    (void)val;
    (void)priv_data;
    return ESP_OK;
}

esp_err_t identify_callback(identification::callback_type_t type, uint16_t endpoint_id,
                            uint8_t effect_id, uint8_t effect_variant, void *priv_data)
{
    ESP_LOGI(TAG, "Identify: endpoint=%u effect=%u", endpoint_id, effect_id);
    return ESP_OK;
}

void matter_event_callback(const chip::DeviceLayer::ChipDeviceEvent *event, intptr_t arg)
{
    switch (event->Type) {
    case chip::DeviceLayer::DeviceEventType::kThreadStateChange:
        s_indicator.thread_attached = chip::DeviceLayer::ConnectivityMgr().IsThreadAttached();
        ESP_LOGI(TAG, "Thread state change: attached=%d",
                 static_cast<int>(s_indicator.thread_attached));
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
        ESP_LOGI(TAG, "Commissioning complete");
        update_thread_state_cache();
        log_commissioning_state("commissioning-complete");
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStarted:
        ESP_LOGI(TAG, "Commissioning session started");
        log_commissioning_state("session-started");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStopped:
        ESP_LOGI(TAG, "Commissioning session stopped");
        log_commissioning_state("session-stopped");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
        s_indicator.window_open = true;
        ESP_LOGI(TAG, "Commissioning window opened");
        log_commissioning_state("window-opened");
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
        s_indicator.window_open = false;
        ESP_LOGI(TAG, "Commissioning window closed");
        log_commissioning_state("window-closed");
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        ESP_LOGI(TAG, "Fabric committed");
        log_commissioning_state("fabric-committed");
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        ESP_LOGI(TAG, "Fabric removed");
        log_commissioning_state("fabric-removed");
        oled_render_status();
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricUpdated:
        ESP_LOGI(TAG, "Fabric updated");
        log_commissioning_state("fabric-updated");
        break;
    case chip::DeviceLayer::DeviceEventType::kCHIPoBLEConnectionClosed:
        ESP_LOGI(TAG, "CHIPoBLE connection closed");
        break;
    case chip::DeviceLayer::DeviceEventType::kDnssdInitialized:
        ESP_LOGI(TAG, "DNS-SD initialized");
        break;
    case chip::DeviceLayer::DeviceEventType::kInterfaceIpAddressChanged:
        ESP_LOGI(TAG, "Interface IP address changed");
        break;
    default:
        break;
    }
}

} // namespace

extern "C" void app_main(void)
{
    s_boot_tick = xTaskGetTickCount();

    ESP_LOGI(TAG, "Starting %s %s + Expansion Board v1.1 - Matter over Thread (firmware %s)",
             kBoardIdentity.vendor_name, kBoardIdentity.product_name,
             kBoardIdentity.software_version_str);
    s_device.reset_reason = esp_reset_reason();
    ESP_LOGI(TAG, "Reset reason: %s", reset_reason_str(s_device.reset_reason));

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    esp_openthread_platform_config_t ot_config = {};
    ot_config.radio_config.radio_mode          = RADIO_MODE_NATIVE;
    ot_config.host_config.host_connection_mode = HOST_CONNECTION_MODE_NONE;
    ot_config.port_config.storage_partition_name = "nvs";
    ot_config.port_config.netif_queue_size       = 10;
    ot_config.port_config.task_queue_size        = 10;
    set_openthread_platform_config(&ot_config);
    ESP_LOGI(TAG, "OpenThread platform config installed");
    ESP_LOGI(TAG, "Expansion Board v1.1 pins: ir_reflective=GPIO%u user_button=GPIO%u buzzer=GPIO%u i2c_sda=GPIO%u i2c_scl=GPIO%u",
             static_cast<unsigned>(kInfraredReflectiveGpio),
             static_cast<unsigned>(kUserButtonGpio),
             static_cast<unsigned>(kExpansionBuzzerGpio),
             static_cast<unsigned>(kExpansionI2cSdaGpio),
             static_cast<unsigned>(kExpansionI2cSclGpio));

    ESP_ERROR_CHECK(init_internal_temperature_sensor());
    ESP_ERROR_CHECK(init_status_led());
    ESP_ERROR_CHECK(init_buzzer());
    ESP_ERROR_CHECK(init_expansion_i2c());
    s_oled_mutex = xSemaphoreCreateMutex();
    if (!s_oled_mutex) {
        ESP_LOGW(TAG, "OLED mutex allocation failed; display updates will run unlocked");
    }
    probe_bma400();
    esp_err_t bma400_tap_ret = init_bma400_double_tap();
    if (bma400_tap_ret != ESP_OK) {
        ESP_LOGW(TAG, "BMA400 double tap detector disabled: %s", esp_err_to_name(bma400_tap_ret));
    }
    esp_err_t oled_ret = init_oled();
    if (oled_ret != ESP_OK) {
        ESP_LOGW(TAG, "OLED initialization skipped: %s", esp_err_to_name(oled_ret));
    }
    ESP_ERROR_CHECK(init_boot_button());
    ESP_ERROR_CHECK(init_infrared_reflective_sensor());
    s_device.button_pressed = read_boot_button_pressed();
    s_device.infrared_reflection = read_infrared_reflection_detected();
    oled_render_status();

    ESP_ERROR_CHECK(bms_node_core::install_device_identity(s_device_info_provider));
    ESP_ERROR_CHECK(init_test_event_trigger_delegate());

    node::config_t node_config;
    node_t *node = node::create(&node_config, attr_callback, identify_callback);
    if (!node) {
        ESP_LOGE(TAG, "Failed to create Matter node");
        return;
    }
    ESP_ERROR_CHECK(ensure_serial_number_attribute());
    ESP_ERROR_CHECK(bms_node_core::thread_diagnostics::ensure_root_identity_attributes(TAG));

    temperature_sensor::config_t temp_config;
    endpoint_t *temp_ep = temperature_sensor::create(node, &temp_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!temp_ep) {
        ESP_LOGE(TAG, "Failed to create temperature_sensor endpoint");
        return;
    }

    generic_switch::config_t button_config;
    button_config.switch_cluster.feature_flags =
        esp_matter::cluster::switch_cluster::feature::momentary_switch::get_id() |
        esp_matter::cluster::switch_cluster::feature::momentary_switch_release::get_id();
    endpoint_t *button_ep = generic_switch::create(node, &button_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!button_ep) {
        ESP_LOGE(TAG, "Failed to create generic_switch endpoint");
        return;
    }

    s_device.temp_ep_id = endpoint::get_id(temp_ep);
    s_device.button_ep_id = endpoint::get_id(button_ep);
    ESP_LOGI(TAG, "Endpoints: temperature=%u button=%u",
             s_device.temp_ep_id,
             s_device.button_ep_id);

    esp_matter::start(matter_event_callback);

    update_thread_state_cache();
    log_commissioning_state("after-start");
    log_onboarding_codes();
    oled_render_status();

    xTaskCreate(indicator_task, "indicator_task", 3072, nullptr, 4, nullptr);
    xTaskCreate(telemetry_task, "telemetry_task", 4096, nullptr, 5, nullptr);
    xTaskCreate(infrared_reflective_task, "ir_reflect_task", 3072, nullptr, 5, nullptr);
    xTaskCreate(bma400_double_tap_task, "bma400_tap_task", 3072, nullptr, 5, nullptr);
    xTaskCreate(button_task, "button_task", 4096, nullptr, 5, nullptr);
}
