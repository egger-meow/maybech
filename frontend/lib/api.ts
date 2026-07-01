export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

import type {
  AccountSnapshotResponse,
  AccountRiskLimitsResponse,
  AccountRiskLimitsUpdate,
  EntryControlResponse,
  AuditEventResponse,
  BTCRegimeResponse,
  ConfirmedPositionFillCreate,
  ConfirmedPositionFillResponse,
  ExecutionFillIngestionStatusResponse,
  InstrumentMetadataListResponse,
  InstrumentContractQuoteRequest,
  InstrumentSizeQuoteRequest,
  InstrumentSizeQuoteResponse,
  LogicalPositionCloseConditionCreate,
  LogicalPositionCloseConditionResponse as LogicalPositionCloseCondition,
  LogicalPositionCloseConditionUpdate,
  LogicalPositionCloseRequest,
  LogicalPositionCloseResponse,
  LogicalPositionReduceRequest,
  LogicalPositionReduceResponse,
  LogicalPositionAllocationResponse,
  LogicalPositionChartResponse,
  LogicalPositionUnitResponse,
  ManualPositionOpenRequest,
  LivePreflightResponse,
  MutationStatusResponse,
  NotificationHealthResponse,
  NotificationTestRequest,
  NotificationTestResponse,
  MarketCandlesResponse,
  PositionBreakEvenCommand,
  PositionGroupResponse,
  PositionProtectionCommand,
  PositionRecoveryAdoptionCommand,
  PositionStopAmendCommand,
  ExternalPositionImportRequest,
  RuntimeEventResponse,
  RuntimeCapabilitiesResponse,
  ServiceStatusResponse,
  SignalEvaluationRequest,
  SignalEvaluationResponse,
  SignalExpressionCreate,
  SignalExpressionResponse,
  SignalExpressionUpdate,
  SignalRuntimeContextResponse,
  SignalTemplateResponse,
  SignalValidationRequest,
  SignalValidationResponse,
  StrategyCreate,
  StrategyDecisionResponse,
  StrategySummaryResponse,
  StrategyUpdate,
  TradeDetailResponse,
  TradeRuleAttach,
  TradeRuleResponse,
  TradeResponse,
} from "./generated/api-types";

export type {
  AccountSnapshotResponse as AccountSnapshot,
  AccountRiskLimitsResponse,
  AccountRiskLimitsUpdate,
  EntryControlResponse,
  AuditEventResponse,
  BTCRegimeResponse as BtcRegime,
  ConfirmedPositionFillCreate,
  ConfirmedPositionFillResponse,
  ExecutionFillIngestionStatusResponse,
  InstrumentMetadataListResponse,
  InstrumentMetadataResponse,
  InstrumentContractQuoteRequest,
  InstrumentSizeQuoteRequest,
  InstrumentSizeQuoteResponse,
  LogicalPositionUnitResponse as LogicalPositionUnit,
  ManualPositionOpenRequest,
  LivePreflightResponse,
  LogicalPositionCloseConditionCreate,
  LogicalPositionCloseConditionResponse as LogicalPositionCloseCondition,
  LogicalPositionCloseConditionUpdate,
  LogicalPositionCloseRequest,
  LogicalPositionCloseResponse,
  LogicalPositionReduceRequest,
  LogicalPositionReduceResponse,
  LogicalPositionAllocationResponse,
  LogicalPositionChartResponse,
  MutationStatusResponse,
  NotificationHealthResponse,
  NotificationTestRequest,
  NotificationTestResponse,
  MarketCandlesResponse,
  PositionBreakEvenCommand,
  PositionGroupResponse,
  PositionProtectionCommand,
  PositionRecoveryAdoptionCommand,
  PositionStopAmendCommand,
  ExternalPositionImportRequest,
  PositionIntentResponse as PositionIntent,
  PositionRuleResponse as PositionRule,
  RuntimeEventResponse as RuntimeEvent,
  RuntimeCapabilitiesResponse,
  ServiceStatusResponse as ServiceStatus,
  SignalEvaluationRequest,
  SignalEvaluationResponse,
  SignalExpressionCreate,
  SignalExpressionResponse as SignalExpression,
  SignalExpressionUpdate,
  SignalRuntimeContextResponse,
  SignalTemplateResponse,
  SignalValidationRequest,
  SignalValidationResponse,
  StrategyCreate,
  StrategyDecisionResponse as StrategyDecision,
  StrategySummaryResponse as StrategySummary,
  StrategyUpdate,
  TradeDetailResponse as TradeDetail,
  TradeRuleAttach,
  TradeRuleResponse as TradeRule,
  TradeResponse,
} from "./generated/api-types";

export type { NotificationChannelHealthResponse } from "./generated/api-types";

export class ApiError extends Error {
  info: unknown;
  status: number;

  constructor(message: string, status: number, info: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.info = info;
  }
}

const apiUrl = (path: string): string => `${API_BASE}${path}`;

let apiToken = "";

export const configureApiToken = (token: string): void => {
  apiToken = token.trim();
};

const requestHeaders = (json = false): HeadersInit => ({
  ...(json ? { "Content-Type": "application/json" } : {}),
  ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
});

export const fetcher = async <T = unknown>(url: string): Promise<T> => {
  const res = await fetch(apiUrl(url), { headers: requestHeaders() });
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError("An error occurred while fetching the data.", res.status, info);
  }
  return res.json();
};

export const postData = async <T = unknown>(url: string, data?: unknown): Promise<T> => {
  const res = await fetch(apiUrl(url), {
    method: "POST",
    headers: requestHeaders(true),
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError(`POST ${url} failed`, res.status, info);
  }
  return res.json();
};

export const patchData = async <T = unknown>(url: string, data?: unknown): Promise<T> => {
  const res = await fetch(apiUrl(url), {
    method: "PATCH",
    headers: requestHeaders(true),
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError(`PATCH ${url} failed`, res.status, info);
  }
  return res.json();
};

export const putData = async <T = unknown>(url: string, data?: unknown): Promise<T> => {
  const res = await fetch(apiUrl(url), {
    method: "PUT",
    headers: requestHeaders(true),
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError(`PUT ${url} failed`, res.status, info);
  }
  return res.json();
};

export const deleteData = async <T = unknown>(url: string): Promise<T> => {
  const res = await fetch(apiUrl(url), {
    method: "DELETE",
    headers: requestHeaders(),
  });
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError(`DELETE ${url} failed`, res.status, info);
  }
  return res.json();
};

export const wsUrl = (path: string): string => {
  const url = new URL(apiUrl(path).replace(/^http/, "ws"));
  if (apiToken) url.searchParams.set("token", apiToken);
  return url.toString();
};

export const getAccountSnapshot = (): Promise<AccountSnapshotResponse> =>
  fetcher<AccountSnapshotResponse>("/account/snapshot");

export const getLivePreflight = (): Promise<LivePreflightResponse> =>
  fetcher<LivePreflightResponse>("/runtime/preflight");

export const getNotificationHealth = (): Promise<NotificationHealthResponse> =>
  fetcher<NotificationHealthResponse>("/notifications/health");

export const sendNotificationTest = (
  payload: NotificationTestRequest,
): Promise<NotificationTestResponse> =>
  postData<NotificationTestResponse>("/notifications/test", payload);

export const getRiskLimits = (): Promise<AccountRiskLimitsResponse> =>
  fetcher<AccountRiskLimitsResponse>("/risk/limits");

export const updateRiskLimits = (
  payload: AccountRiskLimitsUpdate,
): Promise<AccountRiskLimitsResponse> =>
  putData<AccountRiskLimitsResponse>("/risk/limits", payload);

export const getEntryControl = (): Promise<EntryControlResponse> =>
  fetcher<EntryControlResponse>("/risk/entries");

export const listInstruments = (): Promise<InstrumentMetadataListResponse> =>
  fetcher<InstrumentMetadataListResponse>("/instruments");

export const refreshInstruments = (): Promise<InstrumentMetadataListResponse> =>
  postData<InstrumentMetadataListResponse>("/instruments/refresh");

export const quoteInstrumentSize = (
  instId: string,
  payload: InstrumentSizeQuoteRequest,
): Promise<InstrumentSizeQuoteResponse> =>
  postData<InstrumentSizeQuoteResponse>(
    `/instruments/${encodeURIComponent(instId)}/size-quote`,
    payload,
  );

export const quoteInstrumentContracts = (
  instId: string,
  payload: InstrumentContractQuoteRequest,
): Promise<InstrumentSizeQuoteResponse> =>
  postData<InstrumentSizeQuoteResponse>(
    `/instruments/${encodeURIComponent(instId)}/contract-quote`,
    payload,
  );

export const getExecutionFillStatus = (): Promise<ExecutionFillIngestionStatusResponse> =>
  fetcher<ExecutionFillIngestionStatusResponse>("/execution/fills/status");

export const getBtcRegime = (): Promise<BTCRegimeResponse> =>
  fetcher<BTCRegimeResponse>("/market/btc-regime");

export const getMarketCandles = (
  instId: string,
  options: { bar?: string; limit?: number } = {},
): Promise<MarketCandlesResponse> => {
  const params = new URLSearchParams({ inst_id: instId });
  if (options.bar) params.set("bar", options.bar);
  if (options.limit) params.set("limit", String(options.limit));
  return fetcher<MarketCandlesResponse>(`/market/candles?${params.toString()}`);
};

export const listServices = (): Promise<Record<string, ServiceStatusResponse>> =>
  fetcher<Record<string, ServiceStatusResponse>>("/services");

export const getRuntimeCapabilities = (): Promise<RuntimeCapabilitiesResponse> =>
  fetcher<RuntimeCapabilitiesResponse>("/runtime/capabilities");

export const enableService = (name: string): Promise<ServiceStatusResponse> =>
  postData<ServiceStatusResponse>(`/services/${encodeURIComponent(name)}/enable`);

export const disableService = (name: string): Promise<ServiceStatusResponse> =>
  postData<ServiceStatusResponse>(`/services/${encodeURIComponent(name)}/disable`);

export const listStrategyDecisions = (): Promise<StrategyDecisionResponse[]> =>
  fetcher<StrategyDecisionResponse[]>("/strategy/decisions");

export type StrategyDecisionQuery = {
  limit?: number;
  allowed?: boolean;
  executionStatus?: string;
  before?: string;
};

export const listPersistedStrategyDecisions = (
  strategyId: string,
  query: StrategyDecisionQuery = {},
): Promise<StrategyDecisionResponse[]> => {
  const params = new URLSearchParams();
  if (query.limit) params.set("limit", String(query.limit));
  if (query.allowed !== undefined) params.set("allowed", String(query.allowed));
  if (query.executionStatus) params.set("execution_status", query.executionStatus);
  if (query.before) params.set("before", query.before);
  const suffix = params.toString();
  return fetcher<StrategyDecisionResponse[]>(
    `/strategies/${encodeURIComponent(strategyId)}/decisions${suffix ? `?${suffix}` : ""}`,
  );
};

export const listSignalTemplates = (): Promise<SignalTemplateResponse[]> =>
  fetcher<SignalTemplateResponse[]>("/signals/templates");

export type SignalContextOptions = {
  includeCandles?: boolean;
  symbols?: string[];
  bar?: string;
  candleLimit?: number;
};

export const getSignalRuntimeContext = (
  options: SignalContextOptions = {},
): Promise<SignalRuntimeContextResponse> => {
  const params = new URLSearchParams();
  if (options.includeCandles) params.set("include_candles", "true");
  if (options.symbols?.length) params.set("symbols", options.symbols.join(","));
  if (options.bar) params.set("bar", options.bar);
  if (options.candleLimit) params.set("candle_limit", String(options.candleLimit));
  const query = params.toString();
  return fetcher<SignalRuntimeContextResponse>(`/signals/context${query ? `?${query}` : ""}`);
};

export const validateSignal = (payload: SignalValidationRequest): Promise<SignalValidationResponse> =>
  postData<SignalValidationResponse>("/signals/validate", payload);

export const evaluateSignal = (payload: SignalEvaluationRequest): Promise<SignalEvaluationResponse> =>
  postData<SignalEvaluationResponse>("/signals/evaluate", payload);

export const listStrategies = (): Promise<StrategySummaryResponse[]> =>
  fetcher<StrategySummaryResponse[]>("/strategies");

export const createStrategy = (payload: StrategyCreate): Promise<StrategySummaryResponse> =>
  postData<StrategySummaryResponse>("/strategies", payload);

export const updateStrategy = (
  strategyId: string,
  payload: StrategyUpdate,
): Promise<StrategySummaryResponse> =>
  patchData<StrategySummaryResponse>(`/strategies/${encodeURIComponent(strategyId)}`, payload);

export const enableStrategy = (strategyId: string): Promise<StrategySummaryResponse> =>
  postData<StrategySummaryResponse>(`/strategies/${encodeURIComponent(strategyId)}/enable`);

export const disableStrategy = (strategyId: string): Promise<StrategySummaryResponse> =>
  postData<StrategySummaryResponse>(`/strategies/${encodeURIComponent(strategyId)}/disable`);

export const deleteStrategy = (strategyId: string): Promise<MutationStatusResponse> =>
  deleteData<MutationStatusResponse>(`/strategies/${encodeURIComponent(strategyId)}`);

export const listStrategySignals = (strategyId: string): Promise<SignalExpressionResponse[]> =>
  fetcher<SignalExpressionResponse[]>(`/strategies/${encodeURIComponent(strategyId)}/signals`);

export const createStrategySignal = (
  strategyId: string,
  payload: SignalExpressionCreate,
): Promise<SignalExpressionResponse> =>
  postData<SignalExpressionResponse>(`/strategies/${encodeURIComponent(strategyId)}/signals`, payload);

export const getStrategySignal = (
  strategyId: string,
  expressionId: string,
): Promise<SignalExpressionResponse> =>
  fetcher<SignalExpressionResponse>(
    `/strategies/${encodeURIComponent(strategyId)}/signals/${encodeURIComponent(expressionId)}`,
  );

export const updateStrategySignal = (
  strategyId: string,
  expressionId: string,
  payload: SignalExpressionUpdate,
): Promise<SignalExpressionResponse> =>
  patchData<SignalExpressionResponse>(
    `/strategies/${encodeURIComponent(strategyId)}/signals/${encodeURIComponent(expressionId)}`,
    payload,
  );

export const deleteStrategySignal = (
  strategyId: string,
  expressionId: string,
): Promise<MutationStatusResponse> =>
  deleteData<MutationStatusResponse>(
    `/strategies/${encodeURIComponent(strategyId)}/signals/${encodeURIComponent(expressionId)}`,
  );

export type PositionGroupQuery = {
  groupBy?: "instrument_side" | "strategy" | "exchange_position";
  status?: string;
  limit?: number;
};

export const listPositionGroups = (
  query: PositionGroupQuery = {},
): Promise<PositionGroupResponse[]> => {
  const params = new URLSearchParams();
  if (query.groupBy) params.set("group_by", query.groupBy);
  if (query.status) params.set("status", query.status);
  if (query.limit) params.set("limit", String(query.limit));
  const suffix = params.toString();
  return fetcher<PositionGroupResponse[]>(`/positions/groups${suffix ? `?${suffix}` : ""}`);
};

export const importLogicalPosition = (
  payload: ExternalPositionImportRequest,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>("/positions/import", payload);

export const manualOpenPosition = (
  payload: ManualPositionOpenRequest,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>("/positions/manual-open", payload);

export const attachLogicalPositionProtection = (
  positionId: string,
  payload: PositionProtectionCommand,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/protection`,
    payload,
  );

export const adoptRecoveredLogicalPosition = (
  positionId: string,
  payload: PositionRecoveryAdoptionCommand,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/adopt-recovery`,
    payload,
  );

export const amendLogicalPositionStop = (
  positionId: string,
  payload: PositionStopAmendCommand,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/protection/stop`,
    payload,
  );

export const moveLogicalPositionToBreakEven = (
  positionId: string,
  payload: PositionBreakEvenCommand,
): Promise<LogicalPositionUnitResponse> =>
  postData<LogicalPositionUnitResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/break-even`,
    payload,
  );

export const listLogicalPositionCloseConditions = (
  positionId: string,
  enabled?: boolean,
): Promise<LogicalPositionCloseCondition[]> => {
  const query = enabled === undefined ? "" : `?enabled=${enabled ? "true" : "false"}`;
  return fetcher<LogicalPositionCloseCondition[]>(
    `/positions/logical/${encodeURIComponent(positionId)}/close-conditions${query}`,
  );
};

export const listLogicalPositions = (
  status = "open",
): Promise<LogicalPositionUnitResponse[]> =>
  fetcher<LogicalPositionUnitResponse[]>(
    `/positions/logical?status=${encodeURIComponent(status)}`,
  );

export const getLogicalPositionChart = (
  positionId: string,
  options: { bar?: string; limit?: number } = {},
): Promise<LogicalPositionChartResponse> => {
  const params = new URLSearchParams();
  if (options.bar) params.set("bar", options.bar);
  if (options.limit) params.set("limit", String(options.limit));
  const suffix = params.toString();
  return fetcher<LogicalPositionChartResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/chart${suffix ? `?${suffix}` : ""}`,
  );
};

export const createLogicalPositionCloseCondition = (
  positionId: string,
  payload: LogicalPositionCloseConditionCreate,
): Promise<LogicalPositionCloseCondition> =>
  postData<LogicalPositionCloseCondition>(
    `/positions/logical/${encodeURIComponent(positionId)}/close-conditions`,
    payload,
  );

export const updateLogicalPositionCloseCondition = (
  positionId: string,
  conditionId: string,
  payload: LogicalPositionCloseConditionUpdate,
): Promise<LogicalPositionCloseCondition> =>
  patchData<LogicalPositionCloseCondition>(
    `/positions/logical/${encodeURIComponent(positionId)}/close-conditions/${encodeURIComponent(conditionId)}`,
    payload,
  );

export const deleteLogicalPositionCloseCondition = (
  positionId: string,
  conditionId: string,
): Promise<{ status: string }> =>
  deleteData<{ status: string }>(
    `/positions/logical/${encodeURIComponent(positionId)}/close-conditions/${encodeURIComponent(conditionId)}`,
  );

export const listLogicalPositionAllocations = (
  positionId: string,
): Promise<LogicalPositionAllocationResponse[]> =>
  fetcher<LogicalPositionAllocationResponse[]>(
    `/positions/logical/${encodeURIComponent(positionId)}/allocations`,
  );

export const recordConfirmedPositionFill = (
  positionId: string,
  payload: ConfirmedPositionFillCreate,
): Promise<ConfirmedPositionFillResponse> =>
  postData<ConfirmedPositionFillResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/allocations`,
    payload,
  );

export const closeLogicalPosition = (
  positionId: string,
  payload: LogicalPositionCloseRequest,
): Promise<LogicalPositionCloseResponse> =>
  postData<LogicalPositionCloseResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/close`,
    payload,
  );

export const reduceLogicalPosition = (
  positionId: string,
  payload: LogicalPositionReduceRequest,
): Promise<LogicalPositionReduceResponse> =>
  postData<LogicalPositionReduceResponse>(
    `/positions/logical/${encodeURIComponent(positionId)}/reduce`,
    payload,
  );

export const listOpenTrades = (): Promise<TradeDetailResponse[]> =>
  fetcher<TradeDetailResponse[]>("/trades/open");

export const listTradeHistory = (limit = 100): Promise<TradeResponse[]> =>
  fetcher<TradeResponse[]>(`/trades/history?limit=${limit}`);

export const attachTradeRule = (tradeId: string, payload: TradeRuleAttach): Promise<TradeRuleResponse["group"]> =>
  postData<TradeRuleResponse["group"]>(`/trades/${encodeURIComponent(tradeId)}/rules`, payload);

export const deleteTradeRule = (tradeId: string, groupId: string): Promise<{ status: string }> =>
  deleteData<{ status: string }>(
    `/trades/${encodeURIComponent(tradeId)}/rules/${encodeURIComponent(groupId)}`,
  );

export const listRecentEvents = (limit = 50): Promise<RuntimeEventResponse[]> =>
  fetcher<RuntimeEventResponse[]>(`/events?limit=${limit}`);

export type AuditEventQuery = {
  limit?: number;
  eventType?: string;
  source?: string;
  strategyId?: string;
  correlationId?: string;
  positionId?: string;
  tradeId?: string;
  before?: string;
};

export const listAuditEvents = (query: AuditEventQuery = {}): Promise<AuditEventResponse[]> => {
  const params = new URLSearchParams();
  if (query.limit) params.set("limit", String(query.limit));
  if (query.eventType) params.set("event_type", query.eventType);
  if (query.source) params.set("source", query.source);
  if (query.strategyId) params.set("strategy_id", query.strategyId);
  if (query.correlationId) params.set("correlation_id", query.correlationId);
  if (query.positionId) params.set("position_id", query.positionId);
  if (query.tradeId) params.set("trade_id", query.tradeId);
  if (query.before) params.set("before", query.before);
  const suffix = params.toString();
  return fetcher<AuditEventResponse[]>(`/audit/events${suffix ? `?${suffix}` : ""}`);
};
