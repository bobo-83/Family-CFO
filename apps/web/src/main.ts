import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { appConfig } from './app/app.config';
import { configureApiClient } from './app/core/api-client-setup';
import { redirectToCachedLocale } from './app/core/locale-redirect';

// #10: hop to the household's language build BEFORE painting, so a returning
// non-English household never flashes English (see locale-redirect). Angular's
// --localize stamps each build's locale onto <html lang>, which is the only
// locale signal available this early — LOCALE_ID needs the injector.
if (!redirectToCachedLocale(document.documentElement.lang || 'en')) {
  configureApiClient();
  bootstrapApplication(App, appConfig).catch((err) => console.error(err));
}
