import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'kr.maps.mobile',
  appName: 'MAPS',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
  },
}

export default config
