import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
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
      { provide: AuthService, useValue: { role: () => role } },
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
