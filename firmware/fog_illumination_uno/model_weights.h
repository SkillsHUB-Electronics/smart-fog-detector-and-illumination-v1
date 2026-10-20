#pragma once
// AI model: multinomial logistic regression (softmax), runs directly on the Uno.
// The defaults below are hand-tuned weights. Replace them with the output of
// tools/train_model.py after recording your own data.
//
// Classes: 0 = CLEAR, 1 = LIGHT FOG, 2 = DENSE FOG
// Feature order (must match the firmware computeFeatures() and train_model.py):
//   0 temp        = clamp((T - 25) / 10, -2, 3)
//   1 humidity    = clamp((RH - 70) / 20, -3.5, 1.5)
//   2 dew_spread  = clamp((T - dew point) / 5, 0, 4)
//   3 light       = LDR reading 0..1
//   4 attenuation = clamp(1 - laser signal / clear-air baseline, -0.2, 1.2)
#define MODEL_N_FEATURES 5
#define MODEL_N_CLASSES 3
static const float MODEL_W[MODEL_N_CLASSES][MODEL_N_FEATURES] = {
  {0.00f, 0.00f, 0.00f, 0.00f, 0.00f},
  {0.00f, 0.50f, -0.50f, 0.00f, 10.00f},
  {0.00f, 0.80f, -0.80f, 0.00f, 20.00f},
};
static const float MODEL_B[MODEL_N_CLASSES] = {0.0000f, -2.4000f, -8.1400f};

// Light-condition model (softmax on the light feature only): 0 = DAY, 1 = TWILIGHT, 2 = NIGHT.
// Hand-tuned: night below ~0.12, day above ~0.50 of the calibrated LDR range.
#define LIGHT_N_CLASSES 3
static const float LIGHT_W[LIGHT_N_CLASSES] = {30.0000f, 0.0000f, -30.0000f};
static const float LIGHT_B[LIGHT_N_CLASSES] = {-15.0000f, 0.0000f, 3.6000f};
