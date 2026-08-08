import type {
  AccountType,
  HouseholdRole,
  ImportSourceType,
  RecurringFrequency,
} from '../api-client';

/**
 * Human labels for the server's enum CODES (#20).
 *
 * The wire value is an identifier (`auto_loan`, `needs_review`) — fine for the
 * API, wrong on a screen. These maps existed per-page, so the SAME enum read
 * "every two weeks" on Goals and `biweekly` on Bills; one definition per enum
 * is what stops that drifting again.
 *
 * Codes are never translated, only their labels: the code stays the value sent
 * back to the server, compared in class bindings, and used in `track`.
 *
 * `labelFor` falls back to the raw code rather than blanking, so a value the
 * server adds later degrades to today's behaviour instead of vanishing.
 */

export function labelFor<T extends string>(labels: Record<T, string>, code: T | string): string {
  return (labels as Record<string, string>)[code] ?? code;
}

export const FREQUENCY_LABELS: Record<RecurringFrequency, string> = {
  weekly: $localize`:Cadence|How often something repeats:weekly`,
  biweekly: $localize`:Cadence|How often something repeats:every two weeks`,
  semimonthly: $localize`:Cadence|How often something repeats:twice a month`,
  monthly: $localize`:Cadence|How often something repeats:monthly`,
  quarterly: $localize`:Cadence|How often something repeats:quarterly`,
  semiannual: $localize`:Cadence|How often something repeats:twice a year`,
  annual: $localize`:Cadence|How often something repeats:yearly`,
};

export const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = {
  checking: $localize`:Account type|Everyday spending account:Checking`,
  savings: $localize`:Account type|Interest-bearing deposit account:Savings`,
  credit_card: $localize`:Account type|Revolving credit account:Credit card`,
  brokerage: $localize`:Account type|Taxable investment account:Brokerage`,
  retirement: $localize`:Account type|Retirement account such as a 401(k) or IRA:Retirement`,
  hsa: $localize`:Account type|Health savings account:HSA`,
  '529': $localize`:Account type|Education savings plan:529 college savings`,
  mortgage: $localize`:Account type|Home loan:Mortgage`,
  auto_loan: $localize`:Account type|Car loan or lease:Auto loan`,
  student_loan: $localize`:Account type|Education loan:Student loan`,
  '401k_loan': $localize`:Account type|Loan taken against a retirement account:401(k) loan`,
  real_estate: $localize`:Account type|Property held as an asset:Real estate`,
  other_asset: $localize`:Account type|Any other thing owned:Other asset`,
  other_liability: $localize`:Account type|Any other money owed:Other debt`,
};

export type ImportStatus =
  | 'pending'
  | 'processing'
  | 'needs_review'
  | 'completed'
  | 'discarded'
  | 'failed';

export const IMPORT_STATUS_LABELS: Record<ImportStatus, string> = {
  pending: $localize`:Import status|Queued, not started yet:Waiting`,
  processing: $localize`:Import status|Currently being read:Reading`,
  needs_review: $localize`:Import status|Finished but needs the user to check it:Needs review`,
  completed: $localize`:Import status|Finished successfully:Done`,
  discarded: $localize`:Import status|Thrown away by the user:Discarded`,
  failed: $localize`:Import status|Could not be read:Failed`,
};

export const IMPORT_SOURCE_LABELS: Record<ImportSourceType, string> = {
  // File formats are proper names, not words — they stay as they are, but they
  // are uppercased so a table reads "CSV" rather than "csv".
  csv: 'CSV',
  pdf: 'PDF',
  ofx: 'OFX',
  qfx: 'QFX',
};

export const ROLE_LABELS: Record<HouseholdRole, string> = {
  owner: $localize`:Household role|Runs the household and its settings:Owner`,
  adult: $localize`:Household role|Adult member with full day-to-day access:Adult`,
  viewer: $localize`:Household role|Can look but not change anything:Viewer`,
  child: $localize`:Household role|Limited access for a child:Child`,
};
