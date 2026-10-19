#pragma once
// ============================================================================
//  Sensor calibration (linearisation). Written by the dashboard (Calibration
//  page); you can also edit it by hand. Defaults = no correction.
//  After changing it, re-upload the firmware.
//
//    LDR light feature  = clamp((raw - CAL_LDR_LO) / (CAL_LDR_HI - CAL_LDR_LO), 0, 1)
//    temperature [C]    = DHT11 reading * CAL_TEMP_GAIN + CAL_TEMP_OFFSET
//    humidity [%RH]     = DHT11 reading * CAL_HUM_GAIN  + CAL_HUM_OFFSET   (limited to 1..100)
//  The laser baseline is stored in EEPROM (calibrate with the CAL button), not here.
// ============================================================================
#define CAL_LDR_LO      0      // LDR reading (ADC counts) in the darkest condition  -> 0 %
#define CAL_LDR_HI      1023   // LDR reading (ADC counts) in the brightest condition -> 100 %
#define CAL_TEMP_GAIN   1.0f
#define CAL_TEMP_OFFSET 0.0f
#define CAL_HUM_GAIN    1.0f
#define CAL_HUM_OFFSET  0.0f
