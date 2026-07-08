import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: '../../shared/openapi/family-cfo.v1.yaml',
  output: 'src/app/api-client',
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
});
