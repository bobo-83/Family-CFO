import { Injectable } from '@angular/core';
import {
  applyAiModelSelection,
  applyImport,
  createAccount,
  createAuthSession,
  createBackup,
  createBill,
  createBudget,
  createCategory,
  createChatMessage,
  createConnection,
  createGoal,
  createHousehold,
  createImport,
  createIncomeSource,
  createMember,
  createMemory,
  createPairingSession,
  createTransaction,
  deleteAccount,
  deleteConnection,
  deleteConversation,
  deleteBill,
  deleteBudget,
  deleteCategory,
  deleteIncomeSource,
  deleteMember,
  deleteMemory,
  deleteTransaction,
  discardImport,
  dismissBillSuggestion,
  generateReport,
  getAiApplyStatus,
  getAiRuntimeConfig,
  getAiHardwareProfile,
  getAiRuntimeStatus,
  getConversation,
  getHouseholdContext,
  getIncomeAnalysis,
  setIncomeOverride,
  updateHousehold,
  updateIncomeTaxSettings,
  getReport,
  listAccounts,
  listAuditEvents,
  listBackups,
  listBills,
  listBillSuggestions,
  listBudgets,
  listCategories,
  listAiModels,
  listConnections,
  listConversations,
  listGoals,
  listImports,
  listIncomeSources,
  listMembers,
  listMemories,
  listPairedDevices,
  listReports,
  listTransactions,
  recordAccountBalance,
  restoreBackup,
  searchAiModels,
  syncConnection,
  revokePairedDevice,
  updateAccount,
  updateAiRuntimeConfig,
  updateBill,
  updateBudget,
  updateIncomeSource,
  updateMemberRole,
  updateTransaction,
  uploadImportFile,
  type AccountCreateRequest,
  type AiApplyRequest,
  type AccountUpdateRequest,
  type AiRuntimeConfig,
  type AuthSessionCreateRequest,
  type BillCreateRequest,
  type BillUpdateRequest,
  type BudgetCreateRequest,
  type BudgetUpdateRequest,
  type CategoryCreateRequest,
  type ChatRequest,
  type ConnectionCreateRequest,
  type GoalCreateRequest,
  type HouseholdCreateRequest,
  type HouseholdUpdateRequest,
  type ImportCreateRequest,
  type IncomeCreateRequest,
  type IncomeTaxSettingsRequest,
  type IncomeUpdateRequest,
  type MemberCreateRequest,
  type MemberRoleUpdateRequest,
  type MemoryCreateRequest,
  type ReportGenerateRequest,
  type TransactionCreateRequest,
  type TransactionUpdateRequest,
} from '../api-client';

/**
 * A thin wrapper around the generated client's SDK functions.
 *
 * Angular's Vitest test runner does not support `vi.mock()` on relative
 * imports ("Please use Angular TestBed for mocking dependencies"), so
 * components depend on this injectable service instead of importing
 * generated functions directly — tests substitute it via DI.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  login(body: AuthSessionCreateRequest) {
    return createAuthSession({ body });
  }

  createHousehold(body: HouseholdCreateRequest) {
    return createHousehold({ body });
  }

  getHouseholdContext() {
    return getHouseholdContext();
  }

  updateHousehold(body: HouseholdUpdateRequest) {
    return updateHousehold({ body });
  }

  // --- Advisor chat ---
  createChatMessage(body: ChatRequest) {
    return createChatMessage({ body });
  }

  listConversations() {
    return listConversations();
  }

  getConversation(conversationId: string) {
    return getConversation({ path: { conversation_id: conversationId } });
  }

  deleteConversation(conversationId: string) {
    return deleteConversation({ path: { conversation_id: conversationId } });
  }

  // --- Accounts ---
  listAccounts() {
    return listAccounts();
  }

  createAccount(body: AccountCreateRequest) {
    return createAccount({ body });
  }

  updateAccount(accountId: string, body: AccountUpdateRequest) {
    return updateAccount({ path: { account_id: accountId }, body });
  }

  deleteAccount(accountId: string) {
    return deleteAccount({ path: { account_id: accountId } });
  }

  recordAccountBalance(accountId: string, amountMinor: number, currency: string) {
    return recordAccountBalance({
      path: { account_id: accountId },
      body: { balance: { amount_minor: amountMinor, currency } },
    });
  }

  // --- Goals ---
  listGoals() {
    return listGoals();
  }

  createGoal(body: GoalCreateRequest) {
    return createGoal({ body });
  }

  // --- Transactions ---
  listTransactions() {
    return listTransactions();
  }

  createTransaction(body: TransactionCreateRequest) {
    return createTransaction({ body });
  }

  updateTransaction(transactionId: string, body: TransactionUpdateRequest) {
    return updateTransaction({ path: { transaction_id: transactionId }, body });
  }

  deleteTransaction(transactionId: string) {
    return deleteTransaction({ path: { transaction_id: transactionId } });
  }

  // --- Budgets (M46) ---
  listBudgets() {
    return listBudgets();
  }

  createBudget(body: BudgetCreateRequest) {
    return createBudget({ body });
  }

  updateBudget(budgetId: string, body: BudgetUpdateRequest) {
    return updateBudget({ path: { budget_id: budgetId }, body });
  }

  deleteBudget(budgetId: string) {
    return deleteBudget({ path: { budget_id: budgetId } });
  }

  // --- Categories (M45) ---
  listCategories() {
    return listCategories();
  }

  createCategory(body: CategoryCreateRequest) {
    return createCategory({ body });
  }

  deleteCategory(categoryId: string) {
    return deleteCategory({ path: { category_id: categoryId } });
  }

  // --- Bills ---
  listBills() {
    return listBills();
  }

  listBillSuggestions() {
    return listBillSuggestions();
  }

  dismissBillSuggestion(merchantKey: string) {
    return dismissBillSuggestion({ body: { merchant_key: merchantKey } });
  }

  createBill(body: BillCreateRequest) {
    return createBill({ body });
  }

  updateBill(billId: string, body: BillUpdateRequest) {
    return updateBill({ path: { bill_id: billId }, body });
  }

  deleteBill(billId: string) {
    return deleteBill({ path: { bill_id: billId } });
  }

  // --- Advisor memory (M57) ---
  listMemories() {
    return listMemories();
  }

  createMemory(body: MemoryCreateRequest) {
    return createMemory({ body });
  }

  deleteMemory(memoryId: string) {
    return deleteMemory({ path: { memory_id: memoryId } });
  }

  // --- Income ---
  listIncomeSources() {
    return listIncomeSources();
  }

  // --- Income analysis + tax estimate (M61) ---
  getIncomeAnalysis() {
    return getIncomeAnalysis();
  }

  setIncomeOverride(transactionId: string, verdict: 'include' | 'exclude' | 'clear') {
    return setIncomeOverride({ body: { transaction_id: transactionId, verdict } });
  }

  updateIncomeTaxSettings(body: IncomeTaxSettingsRequest) {
    return updateIncomeTaxSettings({ body });
  }

  createIncomeSource(body: IncomeCreateRequest) {
    return createIncomeSource({ body });
  }

  updateIncomeSource(incomeId: string, body: IncomeUpdateRequest) {
    return updateIncomeSource({ path: { income_id: incomeId }, body });
  }

  deleteIncomeSource(incomeId: string) {
    return deleteIncomeSource({ path: { income_id: incomeId } });
  }

  // --- Institution connections (M27) ---
  listConnections() {
    return listConnections();
  }

  createConnection(body: ConnectionCreateRequest) {
    return createConnection({ body });
  }

  deleteConnection(connectionId: string) {
    return deleteConnection({ path: { connection_id: connectionId } });
  }

  syncConnection(connectionId: string) {
    return syncConnection({ path: { connection_id: connectionId } });
  }

  // --- Imports & documents ---
  listImports() {
    return listImports();
  }

  createImport(body: ImportCreateRequest) {
    return createImport({ body });
  }

  uploadImportFile(importId: string, file: Blob) {
    return uploadImportFile({ path: { import_id: importId }, body: { file } });
  }

  applyImport(importId: string) {
    return applyImport({ path: { import_id: importId } });
  }

  discardImport(importId: string) {
    return discardImport({ path: { import_id: importId } });
  }

  // --- Reports ---
  listReports() {
    return listReports();
  }

  getReport(reportId: string) {
    return getReport({ path: { report_id: reportId } });
  }

  generateReport(body: ReportGenerateRequest) {
    return generateReport({ body });
  }

  // --- Backups ---
  listBackups() {
    return listBackups();
  }

  createBackup() {
    return createBackup();
  }

  restoreBackup(backupId: string) {
    return restoreBackup({ path: { backup_id: backupId } });
  }

  // --- Members ---
  listMembers() {
    return listMembers();
  }

  createMember(body: MemberCreateRequest) {
    return createMember({ body });
  }

  updateMemberRole(userId: string, body: MemberRoleUpdateRequest) {
    return updateMemberRole({ path: { user_id: userId }, body });
  }

  deleteMember(userId: string) {
    return deleteMember({ path: { user_id: userId } });
  }

  // --- Audit ---
  listAuditEvents() {
    return listAuditEvents();
  }

  // --- Pairing / devices ---
  createPairingSession() {
    return createPairingSession();
  }

  listPairedDevices() {
    return listPairedDevices();
  }

  revokePairedDevice(deviceId: string) {
    return revokePairedDevice({ path: { device_id: deviceId } });
  }

  getAiRuntimeConfig() {
    return getAiRuntimeConfig();
  }

  getAiRuntimeStatus() {
    return getAiRuntimeStatus();
  }

  listAiModels() {
    return listAiModels();
  }

  getAiHardwareProfile() {
    return getAiHardwareProfile();
  }

  searchAiModels(options: {
    q?: string;
    pipeline?: 'any' | 'text-generation' | 'image-text-to-text';
    limit?: number;
    deep?: boolean;
  }) {
    return searchAiModels({ query: options });
  }

  applyAiModelSelection(body: AiApplyRequest) {
    return applyAiModelSelection({ body });
  }

  getAiApplyStatus() {
    return getAiApplyStatus();
  }

  updateAiRuntimeConfig(body: AiRuntimeConfig) {
    return updateAiRuntimeConfig({ body });
  }
}
