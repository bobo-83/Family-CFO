import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { IncomeTax } from './income-tax';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function txn(id: string, name: string, amountMinor: number, excluded = false) {
  return {
    transaction_id: id,
    occurred_at: '2026-06-15',
    amount: { amount_minor: amountMinor, currency: 'USD' },
    name,
    merchant: name,
    description: `${name} / Payment: Credit ref ${id}`,
    account_name: 'Rewards Checking (0603)',
    excluded,
  };
}

function analysis(overrides: Record<string, unknown> = {}) {
  return {
    sources: [
      {
        source_key: 'acme corp payroll',
        name: 'ACME CORP PAYROLL',
        frequency: 'biweekly',
        manually_added: false,
        typical_amount: { amount_minor: 461_538, currency: 'USD' },
        total_amount: { amount_minor: 2_769_228, currency: 'USD' },
        transactions: [txn('t1', 'ACME CORP PAYROLL', 461_538)],
      },
    ],
    other_inflows: [txn('t9', 'VENMO CASHOUT', 90_000)],
    rollup: {
      annual_income: { amount_minor: 2_769_228, currency: 'USD' },
      monthly_average: { amount_minor: 230_769, currency: 'USD' },
      transaction_count: 6,
      window_days: 365,
    },
    tax: {
      tax_year: 2026,
      filing_status: 'married_joint',
      income_treated_as_net: true,
      gross_income: { amount_minor: 3_200_000, currency: 'USD' },
      net_income: { amount_minor: 2_769_228, currency: 'USD' },
      standard_deduction: { amount_minor: 3_220_000, currency: 'USD' },
      taxable_income: { amount_minor: 0, currency: 'USD' },
      federal_income_tax: { amount_minor: 0, currency: 'USD' },
      fica_tax: { amount_minor: 244_800, currency: 'USD' },
      total_tax: { amount_minor: 244_800, currency: 'USD' },
      effective_rate: 0.0765,
      assumptions: ['State and local taxes are NOT included.'],
    },
    ...overrides,
  };
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [IncomeTax],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

describe('IncomeTax', () => {
  it('renders sources with evidence, the rollup, and the tax estimate', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('ACME CORP PAYROLL');
    expect(host.textContent).toContain('biweekly');
    expect(host.textContent).toContain('USD 27,692.28'); // rollup annual
    expect(host.textContent).toContain('Estimated tax for 2026');
    expect(host.textContent).toContain('USD 2,448.00'); // total tax
    expect(host.textContent).toContain('VENMO CASHOUT');
    // Viewer cannot edit.
    expect(host.querySelector('.income-txns__remove')).toBeNull();
    expect(host.querySelector('.income-txns__add')).toBeNull();
  });

  it('shows the coverage warning, rejects a deposit, and lists rejected ones (M63)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi
        .fn()
        .mockResolvedValueOnce(
          response(
            analysis({
              coverage_warning: 'Synced history only starts Apr 15, 2026 — not a full year of data.',
              other_inflows: [txn('t9', 'VENMO CASHOUT', 90_000), txn('t8', 'REFUND', 5_000, true)],
            }),
          ),
        )
        .mockResolvedValueOnce(response(analysis({ other_inflows: [] }))),
      setIncomeOverride: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('not a full year of data');
    expect(host.textContent).toContain('Rejected deposits (1)');

    (host.querySelector('.income-txns__reject') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.setIncomeOverride).toHaveBeenCalledWith('t9', 'exclude');
  });

  it('expands a deposit row to its full evidence (M62)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const otherRow = host.querySelector('.income-txns--other .income-txn') as HTMLDetailsElement;
    otherRow.open = true;
    fixture.detectChanges();

    const details = otherRow.querySelector('.income-txn__details') as HTMLElement;
    expect(details.textContent).toContain('VENMO CASHOUT / Payment: Credit ref t9');
    expect(details.textContent).toContain('Rewards Checking (0603)');
    expect(details.textContent).toContain('From / payer');
    expect(details.textContent).toContain('Deposited into');
  });

  it('removes a deposit from a source and reloads', async () => {
    const apiMock = {
      getIncomeAnalysis: vi
        .fn()
        .mockResolvedValueOnce(response(analysis()))
        .mockResolvedValueOnce(response(analysis({ sources: [] }))),
      setIncomeOverride: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.income-txns__remove') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.setIncomeOverride).toHaveBeenCalledWith('t1', 'exclude');
    expect(apiMock.getIncomeAnalysis).toHaveBeenCalledTimes(2);
  });

  it('adds a missed deposit as income', async () => {
    const apiMock = {
      getIncomeAnalysis: vi
        .fn()
        .mockResolvedValueOnce(response(analysis()))
        .mockResolvedValueOnce(response(analysis({ other_inflows: [] }))),
      setIncomeOverride: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.income-txns__add') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.setIncomeOverride).toHaveBeenCalledWith('t9', 'include');
  });

  it('puts the W2 scan first in the add-earner flow and prefills without saving (M76)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
      scanW2: vi.fn().mockResolvedValue(
        response({
          year: 2025,
          employer: 'ACME CORP',
          wages_minor: 38_541_260,
          federal_withheld_minor: 7_890_315,
          note: 'Read by the on-box photo model — CONFIRM every value.',
        }),
      ),
      createIncomeEarner: vi.fn(),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const form = host.querySelector('.earner-form') as HTMLElement;
    // The scan row is the first element, and the W2 fields sit above the
    // compensation fields — the scan reads as the on-ramp, not an appendix.
    expect(form.firstElementChild?.classList.contains('earner-form__scan-row')).toBe(true);
    const w2Box = form.querySelector('.earner-form__w2') as HTMLElement;
    const baseInput = form.querySelector('input[name=earnerBase]') as HTMLElement;
    expect(w2Box.compareDocumentPosition(baseInput) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(form.textContent).toContain('Nothing is saved until you press');

    const fileInput = form.querySelector('.earner-form__scan input[type=file]') as HTMLInputElement;
    // M77: payroll providers hand out W2s as PDFs — the picker must allow them.
    expect(fileInput.getAttribute('accept')).toContain('application/pdf');
    Object.defineProperty(fileInput, 'files', {
      value: [new File(['w2'], 'w2.jpg', { type: 'image/jpeg' })],
    });
    fileInput.dispatchEvent(new Event('change'));
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect((fixture.nativeElement as HTMLElement).textContent).toContain('CONFIRM every value');
    });

    // Scan prefilled the form as candidates…
    const cmp = fixture.componentInstance as unknown as {
      earnerForm: { label: string; w2Year: number | null; w2Wages: number | null; w2Withheld: number | null };
    };
    expect(cmp.earnerForm.label).toBe('ACME CORP');
    expect(cmp.earnerForm.w2Year).toBe(2025);
    expect(cmp.earnerForm.w2Wages).toBeCloseTo(385_412.6);
    expect(cmp.earnerForm.w2Withheld).toBeCloseTo(78_903.15);
    // …and the guided next step is shown, but NOTHING was saved.
    expect(host.textContent).toContain('Review the fields below, then press');
    expect(apiMock.createIncomeEarner).not.toHaveBeenCalled();
  });

  it('pastes a copied W2 into the same scan path (M114, ADR 0028)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
      scanW2: vi.fn().mockResolvedValue(
        response({ year: 2025, employer: 'ACME CORP', note: 'Read.' }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const file = new File(['w2'], 'w2.png', { type: 'image/png' });
    const cmp = fixture.componentInstance as unknown as {
      onPaste(event: ClipboardEvent): Promise<void>;
    };
    let prevented = false;
    await cmp.onPaste({
      clipboardData: { items: [{ kind: 'file', getAsFile: () => file }] },
      preventDefault: () => {
        prevented = true;
      },
    } as unknown as ClipboardEvent);

    expect(prevented).toBe(true);
    expect(apiMock.scanW2).toHaveBeenCalledWith(
      expect.objectContaining({ image_media_type: 'image/png' }),
    );
  });

  it('ignores a paste for a viewer role (M114)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
      scanW2: vi.fn(),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();

    const file = new File(['w2'], 'w2.png', { type: 'image/png' });
    const cmp = fixture.componentInstance as unknown as {
      onPaste(event: ClipboardEvent): Promise<void>;
    };
    await cmp.onPaste({
      clipboardData: { items: [{ kind: 'file', getAsFile: () => file }] },
      preventDefault: () => {},
    } as unknown as ClipboardEvent);

    expect(apiMock.scanW2).not.toHaveBeenCalled();
  });

  it('labels the compensation profile as pre-tax (M79)', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(
        response(
          analysis({
            profile: {
              earners: [
                {
                  id: 'e1',
                  label: 'Alex',
                  base_salary: { amount_minor: 20_000_000, currency: 'USD' },
                  rsu_annual: { amount_minor: 16_000_000, currency: 'USD' },
                  rsu_frequency: 'quarterly',
                  bonus_percent: 25,
                },
              ],
              expected_annual_gross: { amount_minor: 41_000_000, currency: 'USD' },
              expected_events: [
                {
                  date: '2026-08-12',
                  label: 'Alex RSU vest',
                  amount: { amount_minor: 4_000_000, currency: 'USD' },
                },
              ],
            },
          }),
        ),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Upcoming income (pre-tax)');
    expect(host.textContent).toContain('withhold shares for taxes at vest');
    expect(host.textContent).toContain('RSU value (USD/yr, pre-tax)');
    expect(host.textContent).toContain('pre-tax (gross) amounts');
  });

  it('saves tax settings and recalculates', async () => {
    const apiMock = {
      getIncomeAnalysis: vi.fn().mockResolvedValue(response(analysis())),
      updateIncomeTaxSettings: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(IncomeTax);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const select = host.querySelector('.tax-card__settings select') as HTMLSelectElement;
    select.value = 'single';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    (host.querySelector('.tax-card__settings button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.updateIncomeTaxSettings).toHaveBeenCalledWith({
      tax_filing_status: 'single',
      income_treated_as_net: true,
    });
    expect(apiMock.getIncomeAnalysis).toHaveBeenCalledTimes(2);
  });
});
