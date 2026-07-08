import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { appConfig } from './app/app.config';
import { configureApiClient } from './app/core/api-client-setup';

configureApiClient();

bootstrapApplication(App, appConfig).catch((err) => console.error(err));
