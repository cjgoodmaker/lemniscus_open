"use strict";

const sax = require("sax");
const fs = require("fs");

// Apple Health type → [modality, short_name]
const HEALTH_TYPE_MAP = {
  // Activity
  "HKQuantityTypeIdentifierStepCount": ["activity", "Steps"],
  "HKQuantityTypeIdentifierDistanceWalkingRunning": ["activity", "Distance"],
  "HKQuantityTypeIdentifierActiveEnergyBurned": ["activity", "ActiveEnergy"],
  "HKQuantityTypeIdentifierBasalEnergyBurned": ["activity", "BasalEnergy"],
  "HKQuantityTypeIdentifierFlightsClimbed": ["activity", "FlightsClimbed"],
  "HKQuantityTypeIdentifierAppleExerciseTime": ["activity", "ExerciseTime"],
  "HKQuantityTypeIdentifierAppleStandTime": ["activity", "StandTime"],
  // Vitals
  "HKQuantityTypeIdentifierHeartRate": ["vitals", "HeartRate"],
  "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ["vitals", "HRV"],
  "HKQuantityTypeIdentifierRestingHeartRate": ["vitals", "RestingHR"],
  "HKQuantityTypeIdentifierWalkingHeartRateAverage": ["vitals", "WalkingHR"],
  "HKQuantityTypeIdentifierBloodPressureSystolic": ["vitals", "BPSystolic"],
  "HKQuantityTypeIdentifierBloodPressureDiastolic": ["vitals", "BPDiastolic"],
  "HKQuantityTypeIdentifierOxygenSaturation": ["vitals", "SpO2"],
  "HKQuantityTypeIdentifierBloodGlucose": ["vitals", "BloodGlucose"],
  "HKQuantityTypeIdentifierRespiratoryRate": ["vitals", "RespiratoryRate"],
  // Body
  "HKQuantityTypeIdentifierBodyMass": ["body", "Weight"],
  "HKQuantityTypeIdentifierHeight": ["body", "Height"],
  "HKQuantityTypeIdentifierBodyMassIndex": ["body", "BMI"],
  "HKQuantityTypeIdentifierBodyFatPercentage": ["body", "BodyFat"],
  "HKQuantityTypeIdentifierLeanBodyMass": ["body", "LeanMass"],
  // Sleep
  "HKCategoryTypeIdentifierSleepAnalysis": ["sleep", "SleepAnalysis"],
  // Nutrition
  "HKQuantityTypeIdentifierDietaryEnergyConsumed": ["nutrition", "Calories"],
  "HKQuantityTypeIdentifierDietaryProtein": ["nutrition", "Protein"],
  "HKQuantityTypeIdentifierDietaryCarbohydrates": ["nutrition", "Carbs"],
  "HKQuantityTypeIdentifierDietaryFatTotal": ["nutrition", "Fat"],
  "HKQuantityTypeIdentifierDietaryWater": ["nutrition", "Water"],
  // Fitness
  "HKQuantityTypeIdentifierVO2Max": ["fitness", "VO2Max"],
  // Mindfulness
  "HKCategoryTypeIdentifierMindfulSession": ["mindfulness", "MindfulSession"],
  // Workouts
  "HKWorkoutTypeIdentifier": ["workout", "Workout"],
};

const DURATION_TYPES = new Set([
  "HKCategoryTypeIdentifierSleepAnalysis",
]);

/**
 * Parse an Apple Health timestamp string into a Date object.
 * Handles formats like "2024-01-15 08:30:00 -0700"
 */
function parseTimestamp(ts) {
  if (!ts) return null;
  try {
    // Apple Health format: "2024-01-15 08:30:00 -0700"
    // Convert to ISO: "2024-01-15T08:30:00-07:00"
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
 * Returns a promise that resolves to { rows, count }.
 * rows are arrays: [sourceId, recordType, modality, shortName, value, unit, timestamp, endTimestamp]
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
      const typeInfo = HEALTH_TYPE_MAP[hkType];
      if (!typeInfo) return;

      const start = parseTimestamp(node.attributes.startDate);
      if (!start) return;

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
      // sax recovers from most errors, just log
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
