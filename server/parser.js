"use strict";

const sax = require("sax");
const fs = require("fs");

// Known Apple Health types → [modality, short_name]
// These get friendly short names. Any type NOT listed here is still indexed
// with an auto-derived short name (e.g. "HKQuantityTypeIdentifierUVExposure" → "UVExposure").
const HEALTH_TYPE_MAP = {
  // Activity
  "HKQuantityTypeIdentifierStepCount": ["activity", "Steps"],
  "HKQuantityTypeIdentifierDistanceWalkingRunning": ["activity", "Distance"],
  "HKQuantityTypeIdentifierDistanceCycling": ["activity", "DistanceCycling"],
  "HKQuantityTypeIdentifierDistanceSwimming": ["activity", "DistanceSwimming"],
  "HKQuantityTypeIdentifierDistanceWheelchair": ["activity", "DistanceWheelchair"],
  "HKQuantityTypeIdentifierDistanceDownhillSnowSports": ["activity", "DistanceSnowSports"],
  "HKQuantityTypeIdentifierActiveEnergyBurned": ["activity", "ActiveEnergy"],
  "HKQuantityTypeIdentifierBasalEnergyBurned": ["activity", "BasalEnergy"],
  "HKQuantityTypeIdentifierFlightsClimbed": ["activity", "FlightsClimbed"],
  "HKQuantityTypeIdentifierAppleExerciseTime": ["activity", "ExerciseTime"],
  "HKQuantityTypeIdentifierAppleMoveTime": ["activity", "MoveTime"],
  "HKQuantityTypeIdentifierAppleStandTime": ["activity", "StandTime"],
  "HKCategoryTypeIdentifierAppleStandHour": ["activity", "StandHour"],
  "HKQuantityTypeIdentifierPushCount": ["activity", "PushCount"],
  "HKQuantityTypeIdentifierSwimmingStrokeCount": ["activity", "SwimmingStrokes"],
  "HKQuantityTypeIdentifierNikeFuel": ["activity", "NikeFuel"],

  // Vitals
  "HKQuantityTypeIdentifierHeartRate": ["vitals", "HeartRate"],
  "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ["vitals", "HRV"],
  "HKQuantityTypeIdentifierRestingHeartRate": ["vitals", "RestingHR"],
  "HKQuantityTypeIdentifierWalkingHeartRateAverage": ["vitals", "WalkingHR"],
  "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute": ["vitals", "HRRecovery"],
  "HKQuantityTypeIdentifierAtrialFibrillationBurden": ["vitals", "AFibBurden"],
  "HKQuantityTypeIdentifierBloodPressureSystolic": ["vitals", "BPSystolic"],
  "HKQuantityTypeIdentifierBloodPressureDiastolic": ["vitals", "BPDiastolic"],
  "HKQuantityTypeIdentifierOxygenSaturation": ["vitals", "SpO2"],
  "HKQuantityTypeIdentifierBloodGlucose": ["vitals", "BloodGlucose"],
  "HKQuantityTypeIdentifierRespiratoryRate": ["vitals", "RespiratoryRate"],
  "HKQuantityTypeIdentifierBodyTemperature": ["vitals", "BodyTemperature"],
  "HKCategoryTypeIdentifierLowHeartRateEvent": ["vitals", "LowHREvent"],
  "HKCategoryTypeIdentifierHighHeartRateEvent": ["vitals", "HighHREvent"],
  "HKCategoryTypeIdentifierIrregularHeartRhythmEvent": ["vitals", "IrregularHREvent"],

  // Body
  "HKQuantityTypeIdentifierBodyMass": ["body", "Weight"],
  "HKQuantityTypeIdentifierHeight": ["body", "Height"],
  "HKQuantityTypeIdentifierBodyMassIndex": ["body", "BMI"],
  "HKQuantityTypeIdentifierBodyFatPercentage": ["body", "BodyFat"],
  "HKQuantityTypeIdentifierLeanBodyMass": ["body", "LeanMass"],
  "HKQuantityTypeIdentifierWaistCircumference": ["body", "WaistCircumference"],

  // Sleep
  "HKCategoryTypeIdentifierSleepAnalysis": ["sleep", "SleepAnalysis"],
  "HKQuantityTypeIdentifierAppleSleepingWristTemperature": ["sleep", "SleepWristTemp"],

  // Nutrition
  "HKQuantityTypeIdentifierDietaryEnergyConsumed": ["nutrition", "Calories"],
  "HKQuantityTypeIdentifierDietaryProtein": ["nutrition", "Protein"],
  "HKQuantityTypeIdentifierDietaryCarbohydrates": ["nutrition", "Carbs"],
  "HKQuantityTypeIdentifierDietaryFatTotal": ["nutrition", "Fat"],
  "HKQuantityTypeIdentifierDietaryWater": ["nutrition", "Water"],

  // Fitness
  "HKQuantityTypeIdentifierVO2Max": ["fitness", "VO2Max"],
  "HKQuantityTypeIdentifierAppleWalkingSteadiness": ["fitness", "WalkingSteadiness"],
  "HKQuantityTypeIdentifierSixMinuteWalkTestDistance": ["fitness", "SixMinWalk"],

  // Mobility
  "HKQuantityTypeIdentifierWalkingSpeed": ["mobility", "WalkingSpeed"],
  "HKQuantityTypeIdentifierWalkingStepLength": ["mobility", "WalkingStepLength"],
  "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": ["mobility", "WalkingAsymmetry"],
  "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": ["mobility", "DoubleSupport"],
  "HKQuantityTypeIdentifierStairAscentSpeed": ["mobility", "StairAscentSpeed"],
  "HKQuantityTypeIdentifierStairDescentSpeed": ["mobility", "StairDescentSpeed"],

  // Running
  "HKQuantityTypeIdentifierRunningSpeed": ["running", "RunningSpeed"],
  "HKQuantityTypeIdentifierRunningStrideLength": ["running", "RunningStride"],
  "HKQuantityTypeIdentifierRunningPower": ["running", "RunningPower"],
  "HKQuantityTypeIdentifierRunningGroundContactTime": ["running", "GroundContactTime"],
  "HKQuantityTypeIdentifierRunningVerticalOscillation": ["running", "VerticalOscillation"],

  // Hearing
  "HKQuantityTypeIdentifierEnvironmentalAudioExposure": ["hearing", "EnvironmentAudio"],
  "HKQuantityTypeIdentifierHeadphoneAudioExposure": ["hearing", "HeadphoneAudio"],
  "HKCategoryTypeIdentifierEnvironmentalAudioExposureEvent": ["hearing", "EnvironmentAudioEvent"],
  "HKCategoryTypeIdentifierHeadphoneAudioExposureEvent": ["hearing", "HeadphoneAudioEvent"],

  // Reproductive health
  "HKCategoryTypeIdentifierMenstrualFlow": ["reproductive", "MenstrualFlow"],
  "HKCategoryTypeIdentifierIntermenstrualBleeding": ["reproductive", "IntermenstrualBleeding"],
  "HKCategoryTypeIdentifierOvulationTestResult": ["reproductive", "OvulationTest"],
  "HKCategoryTypeIdentifierCervicalMucusQuality": ["reproductive", "CervicalMucus"],
  "HKCategoryTypeIdentifierSexualActivity": ["reproductive", "SexualActivity"],
  "HKCategoryTypeIdentifierContraceptive": ["reproductive", "Contraceptive"],
  "HKQuantityTypeIdentifierBasalBodyTemperature": ["reproductive", "BasalBodyTemp"],

  // Mindfulness
  "HKCategoryTypeIdentifierMindfulSession": ["mindfulness", "MindfulSession"],

  // Lab & tests
  "HKQuantityTypeIdentifierBloodAlcoholContent": ["lab", "BloodAlcohol"],
  "HKQuantityTypeIdentifierNumberOfAlcoholicBeverages": ["lab", "AlcoholicBeverages"],
  "HKQuantityTypeIdentifierElectrodermalActivity": ["lab", "ElectrodermalActivity"],
  "HKQuantityTypeIdentifierForcedExpiratoryVolume1": ["lab", "FEV1"],
  "HKQuantityTypeIdentifierForcedVitalCapacity": ["lab", "FVC"],
  "HKQuantityTypeIdentifierInhalerUsage": ["lab", "InhalerUsage"],
  "HKQuantityTypeIdentifierInsulinDelivery": ["lab", "InsulinDelivery"],
  "HKQuantityTypeIdentifierNumberOfTimesFallen": ["lab", "Falls"],
  "HKQuantityTypeIdentifierPeakExpiratoryFlowRate": ["lab", "PeakFlowRate"],
  "HKQuantityTypeIdentifierPeripheralPerfusionIndex": ["lab", "PerfusionIndex"],

  // UV & environment
  "HKQuantityTypeIdentifierUVExposure": ["environment", "UVExposure"],
  "HKQuantityTypeIdentifierUnderwaterDepth": ["environment", "UnderwaterDepth"],
  "HKQuantityTypeIdentifierWaterTemperature": ["environment", "WaterTemperature"],

  // Self care
  "HKCategoryTypeIdentifierToothbrushingEvent": ["selfcare", "Toothbrushing"],
  "HKCategoryTypeIdentifierHandwashingEvent": ["selfcare", "Handwashing"],

  // Workouts
  "HKWorkoutTypeIdentifier": ["workout", "Workout"],
};

// Types where value = duration in minutes computed from (endDate - startDate)
const DURATION_TYPES = new Set([
  "HKCategoryTypeIdentifierSleepAnalysis",
  "HKCategoryTypeIdentifierMindfulSession",
]);

// Prefixes to strip when auto-deriving short names for unknown types
const TYPE_PREFIXES = [
  "HKQuantityTypeIdentifier",
  "HKCategoryTypeIdentifier",
  "HKCorrelationTypeIdentifier",
  "HKDataTypeIdentifier",
];

/**
 * Derive [modality, shortName] for any HK type not in the map.
 * Strips the prefix to create a readable short name.
 */
function deriveTypeInfo(hkType) {
  for (const prefix of TYPE_PREFIXES) {
    if (hkType.startsWith(prefix)) {
      return ["other", hkType.slice(prefix.length)];
    }
  }
  return ["other", hkType];
}

/**
 * Parse an Apple Health timestamp string into a Date object.
 * Handles formats like "2024-01-15 08:30:00 -0700"
 */
function parseTimestamp(ts) {
  if (!ts) return null;
  try {
    const m = ts.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+([+-]\d{2})(\d{2})$/);
    if (m) {
      return new Date(`${m[1]}T${m[2]}${m[3]}:${m[4]}`);
    }
    return new Date(ts);
  } catch {
    return null;
  }
}

function parseValue(v) {
  if (v == null) return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}

/**
 * Stream individual readings from an Apple Health export.xml file.
 * Indexes ALL Record and Workout elements — known types get friendly names,
 * unknown types get auto-derived names from their HK identifier.
 */
function streamRawReadings(filePath, sourceId) {
  return new Promise((resolve, reject) => {
    const rows = [];
    let count = 0;

    const saxStream = sax.createStream(true, { trim: true });

    saxStream.on("opentag", (node) => {
      if (node.name === "Workout") {
        const start = parseTimestamp(node.attributes.startDate);
        if (!start) return;

        const workoutType = (node.attributes.workoutActivityType || "Unknown");
        const duration = node.attributes.duration;
        const end = parseTimestamp(node.attributes.endDate);

        rows.push([
          sourceId,
          "HKWorkoutTypeIdentifier",
          "workout",
          workoutType.replace("HKWorkoutActivityType", ""),
          duration ? Number(duration) : null,
          "min",
          start.toISOString(),
          end ? end.toISOString() : null,
        ]);
        count++;
        return;
      }

      if (node.name !== "Record") return;

      const hkType = node.attributes.type || "";
      if (!hkType) return;

      const start = parseTimestamp(node.attributes.startDate);
      if (!start) return;

      // Look up known type or auto-derive
      const typeInfo = HEALTH_TYPE_MAP[hkType] || deriveTypeInfo(hkType);
      const [modality, shortName] = typeInfo;
      const value = parseValue(node.attributes.value);
      const unit = node.attributes.unit || "";
      const end = parseTimestamp(node.attributes.endDate);

      if (DURATION_TYPES.has(hkType) && start && end) {
        const durationMin = Math.round(((end - start) / 60000) * 10) / 10;
        rows.push([
          sourceId,
          hkType,
          modality,
          shortName,
          durationMin,
          "min",
          start.toISOString(),
          end.toISOString(),
        ]);
      } else {
        rows.push([
          sourceId,
          hkType,
          modality,
          shortName,
          value,
          unit,
          start.toISOString(),
          end ? end.toISOString() : null,
        ]);
      }
      count++;
    });

    saxStream.on("error", (err) => {
      console.error("XML parse warning:", err.message);
      saxStream._parser.error = null;
      saxStream._parser.resume();
    });

    saxStream.on("end", () => {
      console.error(`Streamed ${count} individual readings for bulk storage`);
      resolve({ rows, count });
    });

    fs.createReadStream(filePath).pipe(saxStream);
  });
}

module.exports = { streamRawReadings, HEALTH_TYPE_MAP };
