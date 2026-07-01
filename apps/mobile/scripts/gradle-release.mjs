// gradle-release.mjs
//
// Runs `gradlew assembleRelease` for the generated Android project with a known-good
// JAVA_HOME (JDK 21). Capacitor's Android Gradle Plugin requires JDK 21.
//
// JAVA_HOME resolution order:
//   1. process.env.MAPS_JAVA_HOME  (override)
//   2. process.env.JAVA_HOME       (if already set)
//   3. the pinned default below    (winget EclipseAdoptium.Temurin.21.JDK)
//
// The default matches the machine this project was built on. On other machines set
// MAPS_JAVA_HOME to your JDK 21 install.
//
// Usage:  node scripts/gradle-release.mjs
// Run from apps/mobile/ (npm scripts already set that cwd).

import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const DEFAULT_JDK21 = 'C:\\Program Files\\Eclipse Adoptium\\jdk-21.0.11.10-hotspot'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const mobileDir = join(scriptDir, '..')
const androidDir = join(mobileDir, 'android')

const isWindows = process.platform === 'win32'
const gradlew = join(androidDir, isWindows ? 'gradlew.bat' : 'gradlew')

if (!existsSync(gradlew)) {
  console.error(
    `[gradle-release] ERROR: ${gradlew} not found.\n` +
      `  Generate the Android project first: npm run cap:add:android && npx cap sync android`,
  )
  process.exit(1)
}

const javaHome = process.env.MAPS_JAVA_HOME || process.env.JAVA_HOME || DEFAULT_JDK21
if (!existsSync(javaHome)) {
  console.error(
    `[gradle-release] ERROR: JAVA_HOME does not exist: ${javaHome}\n` +
      `  Install JDK 21 or set MAPS_JAVA_HOME to your JDK 21 path.`,
  )
  process.exit(1)
}

console.log(`[gradle-release] JAVA_HOME = ${javaHome}`)
console.log('[gradle-release] Running: gradlew assembleRelease --no-daemon')

const result = spawnSync(gradlew, ['assembleRelease', '--no-daemon'], {
  cwd: androidDir,
  stdio: 'inherit',
  env: { ...process.env, JAVA_HOME: javaHome },
  // gradlew.bat must run through the shell on Windows.
  shell: isWindows,
})

if (result.status !== 0) {
  console.error(`[gradle-release] assembleRelease failed (exit ${result.status}).`)
  process.exit(result.status ?? 1)
}

console.log(
  '[gradle-release] Done. Signed APK: android/app/build/outputs/apk/release/app-release.apk',
)
