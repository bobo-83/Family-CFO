import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  // Compatibility CI points this at the oldest immutable API fixture for the
  // current contract, then compiles the real app against the generated client.
  input: process.env['FAMILY_CFO_OPENAPI_CONTRACT'] ?? '../../shared/openapi/family-cfo.v1.yaml',
  output: 'src/app/api-client',
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
});
