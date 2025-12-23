/**
 * API Client for Gamma Box Backend
 */

// Dynamically compute API URL - must be called fresh each time to handle SSR/client differences
function getApiUrl(): string {
  // Check for environment variable first
  const envUrl = process.env.NEXT_PUBLIC_API_URL;

  if (envUrl && envUrl.startsWith("http")) {
    return envUrl;
  }

  // Client-side: use Tailscale IP from environment or fallback
  if (typeof window !== "undefined" && window.location) {
    // Always use HTTP for API server to avoid 421 Misdirected Request errors
    // The API server on port 8555 expects HTTP, not HTTPS
    const tailscaleIp = process.env.NEXT_PUBLIC_TAILSCALE_IP || "100.103.163.68";

    return `http://${tailscaleIp}:8555`;
  }

  // Server-side fallback
  return "http://127.0.0.1:8555";
}

export interface AccountInfo {
  accountId: string;
  status: string;
  currency: string;
  buyingPower: number;
  cash: number;
  portfolioValue: number;
  equity: number;
  longMarketValue: number;
  shortMarketValue: number;
  patternDayTrader: boolean;
  dayTradesRemaining: number;
}

export interface Position {
  symbol: string;
  quantity: number;
  side: "long" | "short";
  marketValue: number;
  unrealizedPnl: number;
  unrealizedPnlPercent: number;
  avgEntryPrice: number;
  // Daily P&L (resets each trading day)
  dayPnl: number;
  dayPnlPercent: number;
  // Status: open or closed
  status?: "open" | "closed";
  // For closed positions: realized P&L
  realizedPnl?: number;
  realizedPnlPercent?: number;
  // Close date for closed positions
  closeDate?: string;
}

export interface TodayPnl {
  realized: number;
  unrealized: number;
  total: number;
  currentNetLiq: number | null;
  date: string;
}

export interface TradeDecision {
  hasSetup: boolean;
  direction: "CALL" | "PUT" | null;
  conviction: number;
  pattern: string | null;
  entry: number | null;
  stop: number | null;
  targets: number[];
  reasoning: string;
  checklistPassed: boolean;
}

export interface TradeExecutionResult {
  success: boolean;
  orderId?: string;
  symbol?: string;
  quantity?: number;
  price?: number;
  totalCost?: number;
  error?: string;
  multiAccount?: {
    mode: "paper" | "live";
    total: number;
    successful: number;
    failed: number;
    results: Array<{
      account_id: number;
      account_name: string;
      success: boolean;
      orderId?: string;
      error?: string;
    }>;
  };
}

// Usage tracking types
export interface UsageSummary {
  userId: string;
  tier: string;
  billingCycleStart: string;
  usage: {
    chat_message: { used: number; limit: number; remaining: number };
    trade_analysis: { used: number; limit: number; remaining: number };
    trade_execution: { used: number; limit: number; remaining: number };
  };
  isOverLimit: boolean;
}

export interface UsageCheckResult {
  allowed: boolean;
  remaining: number;
  limit: number;
  overageCharge?: number;
}

class ApiClient {
  // TastyTrade HTTP API configuration (from curl_instructions.md)
  private get tastyTradeHttpApiUrl(): string {
    return process.env.NEXT_PUBLIC_TASTY_HTTP_API_URL || "https://tasty.gammabox.app";
  }

  private get tastyTradeHttpApiKey(): string {
    return process.env.NEXT_PUBLIC_TASTY_HTTP_API_KEY || "";
  }

  constructor() {
    // Constructor - no mode caching needed
  }

  // Get base URL dynamically to handle SSR/client transitions
  private get baseUrl(): string {
    return getApiUrl();
  }


  private async request<T>(
    endpoint: string,
    options?: RequestInit,
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;

    // Debug logging for URL issues
    if (!url.startsWith("http")) {
      console.error(
        "Invalid API URL constructed:",
        url,
        "baseUrl:",
        this.baseUrl,
      );
      throw new Error(`Invalid URL: ${url}`);
    }

    // Log the request for debugging
    if (process.env.NODE_ENV === "development") {
      console.log(`[API] Requesting: ${options?.method || "GET"} ${url}`);
    }

    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...options?.headers,
        },
        // Explicitly set CORS mode - different ports are different origins
        mode: "cors",
        credentials: "include", // Include credentials since CORS allows it
      });

      if (!response.ok) {
        // Handle specific error codes gracefully
        if (response.status === 421) {
          // Misdirected Request - usually a protocol/authority mismatch
          // Try to use http:// explicitly for the API server
          const httpUrl = url.replace(/^https?:/, "http:");

          if (httpUrl !== url) {
            console.warn(`[API] 421 error, retrying with HTTP: ${httpUrl}`);
            // Don't retry automatically to avoid infinite loops, just throw with a clear message
          }
          throw new Error(`MCP server returned 421: Misdirected Request`);
        }

        const errorText = await response
          .text()
          .catch(() => response.statusText);
        let errorData;

        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { error: errorText };
        }
        throw new Error(
          errorData.error || `HTTP ${response.status}: ${response.statusText}`,
        );
      }

      return response.json();
    } catch (error: any) {
      // More detailed error logging, but suppress common connection errors in production
      const errorMsg = error.message || String(error);
      const errorName = error.name || "Unknown";

      // Only log detailed errors for non-connection issues or in development
      const isConnectionError =
        errorMsg.includes("421") ||
        errorMsg.includes("Misdirected") ||
        errorMsg.includes("503") ||
        errorMsg.includes("500") ||
        errorMsg.includes("Failed to fetch") ||
        errorMsg.includes("NetworkError");

      if (!isConnectionError || process.env.NODE_ENV === "development") {
        console.error(`[API] Request failed [${errorName}]: ${errorMsg}`, {
          url,
          endpoint,
          baseUrl: this.baseUrl,
          method: options?.method || "GET",
          error: {
            name: error.name,
            message: error.message,
            stack: error.stack?.split("\n")[0],
          },
        });
      } else {
        // Silent fail for connection errors in production to reduce console noise
        console.warn(`[API] Connection error (suppressed): ${endpoint}`);
      }
      throw error;
    }
  }

  // MCP Server endpoints
  async getMCPHealth(): Promise<{ status: string; mcpServer: string }> {
    return this.request("/api/mcp/health");
  }

  // Get trading mode (live/paper) - always returns "live" since sandbox was removed
  async getTradingMode(): Promise<"live"> {
    // Since sandbox was removed, always return "live"
    return "live";
  }

  // Helper method to get account ID (live accounts only - paper trading removed)
  // Per curl_instructions.md: account_id can be the same as API key
  // The API key from .env should be used as account_id when it matches the pattern
  private async getAccountId(): Promise<string | null> {
    const apiKey = this.tastyTradeHttpApiKey;
    
    // If API key looks like an account ID (alphanumeric), use it directly
    // This matches the pattern where API key = account_id per curl_instructions.md
    if (apiKey && /^[A-Z0-9]+$/.test(apiKey)) {
      console.log(`[API] Using API key as account_id: ${apiKey}`);
      return apiKey;
    }
    
    // Fallback: try to get account_id from internal accounts API (live accounts only)
    try {
      const accounts = await this.getAccounts();
      
      // Find enabled live account (is_sandbox = false)
      const liveAccount = accounts.find(
        (acc) => acc.is_enabled && !acc.is_sandbox
      );
      
      if (liveAccount?.account_id) {
        console.log(`[API] Using account_id from internal API: ${liveAccount.account_id}`);
        return liveAccount.account_id;
      }
    } catch (error) {
      console.warn("[API] Failed to get accounts for account ID lookup:", error);
    }
    
    return null;
  }

  async getAccountInfo(): Promise<AccountInfo> {
    // Use TastyTrade HTTP API at tasty.gammabox.app
    console.log(`[API] 🔍 getAccountInfo: Starting account info fetch via TastyTrade HTTP API...`);
    
    try {
      // Use TastyTrade HTTP API
      const apiKey = this.tastyTradeHttpApiKey;
      if (!apiKey) {
        throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
      }

      // Get account ID (optional if API key has default_account_id configured)
      const accountId = await this.getAccountId();
      
      // Build URL with optional account_id parameter
      const url = accountId 
        ? `${this.tastyTradeHttpApiUrl}/api/v1/balances?account_id=${accountId}`
        : `${this.tastyTradeHttpApiUrl}/api/v1/balances`;
      
      console.log(`[API] 📡 Using TastyTrade HTTP API: ${url}`);
      
      const response = await fetch(url, {
        method: "GET",
        headers: {
          "X-API-Key": apiKey,
          "Content-Type": "application/json",
        },
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => response.statusText);
        let errorData;
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { detail: errorText };
        }
        
        // Extract error message from various possible fields
        const errorMsg = errorData.detail || errorData.error || errorData.message || errorText || `HTTP ${response.status}: ${response.statusText}`;
        throw new Error(errorMsg);
      }

      const data = await response.json();
      
      // Transform TastyTrade HTTP API response to AccountInfo format
      return this.transformTastyTradeBalanceResponse(accountId, data);
    } catch (error: any) {
      console.error("[API] getAccountInfo error:", error);
      throw error;
    }
  }

  // Transform TastyTrade HTTP API balance response to AccountInfo
  private transformTastyTradeBalanceResponse(accountId: string | null, data: any): AccountInfo {
    return {
      accountId: accountId || "unknown",
      status: "ACTIVE",
      currency: "USD",
      buyingPower: parseFloat(data.buying_power || data.net_liquidating_value || "0"),
      cash: parseFloat(data.cash || "0"),
      portfolioValue: parseFloat(data.net_liquidating_value || "0"),
      equity: parseFloat(data.equity || data.net_liquidating_value || "0"),
      longMarketValue: 0,
      shortMarketValue: 0,
      patternDayTrader: false,
      dayTradesRemaining: 0,
    };
  }
  
  private processAccountInfoResponse(
    response: {
      status: string;
      data: any;
      source: string;
      error?: string;
    },
    mode: "paper" | "live"
  ): AccountInfo {
    console.log(
      `[API] 🔄 processAccountInfoResponse: Processing response for mode=${mode}, source=${response.source}`,
    );

    // Handle both direct API response (data directly) and MCP response (wrapped in structuredContent)
    let data = response.data || {};
    console.log(
      `[API] 📦 processAccountInfoResponse: Raw data keys: ${Object.keys(data).join(", ")}`,
    );

    // Check if MCP returned an error
    if (data.isError || data.content) {
      // MCP error format: check content for error messages
      const errorText = data.content?.[0]?.text || "";

      if (
        errorText.includes("Error") ||
        errorText.includes("token_invalid") ||
        errorText.includes("expired")
      ) {
        console.error("[API] MCP error in account info:", errorText);
        // Be specific about token expiration to avoid false positives
        const lowerErrorText = errorText.toLowerCase();
        const isTokenExpired = 
          errorText.includes("token_invalid") ||
          lowerErrorText.includes("token expired") ||
          lowerErrorText.includes("refresh token expired") ||
          lowerErrorText.includes("refresh token is invalid") ||
          lowerErrorText.includes("refresh token is expired");
        
        throw new Error(
          isTokenExpired
            ? "TastyTrade token expired. Please update credentials in Settings."
            : errorText,
        );
      }
    }

    if (data.structuredContent) {
      // MCP format: data is wrapped in structuredContent
      data = data.structuredContent;
    }

    // Check if data is actually empty or invalid
    if (!data || (typeof data === "object" && Object.keys(data).length === 0)) {
      throw new Error(
        "No account data available. Please configure a paper trading account in Settings.",
      );
    }

    // Verify that the returned account matches the expected mode
    const expectedIsSandbox = mode === "paper";
    const returnedIsSandbox =
      data.is_sandbox === true ||
      data.is_sandbox === 1 ||
      data.is_sandbox === "true" ||
      data.is_sandbox === 1;

    console.log(
      `[API] 🔍 processAccountInfoResponse: Mode verification - expectedIsSandbox=${expectedIsSandbox}, returnedIsSandbox=${returnedIsSandbox}, account_number=${data.account_number || data.account_id}`,
    );

    if (returnedIsSandbox !== expectedIsSandbox) {
      console.error(
        `[API] ⚠️ Mode mismatch! Expected is_sandbox=${expectedIsSandbox} (mode=${mode}), but got is_sandbox=${returnedIsSandbox} from account ${data.account_number || data.account_id}`,
      );
      // Don't throw an error, but log a warning - the data might still be useful
      console.warn(
        `[API] ⚠️ Account data mode doesn't match requested mode. This might indicate a configuration issue.`,
      );
    } else {
      console.log(
        `[API] ✅ Account mode verified: is_sandbox=${returnedIsSandbox} matches expected mode=${mode}`,
      );
    }

    // Direct API format: data is already the balance object

    // Parse string values to numbers (TastyTrade API returns strings)
    const parseFloatSafe = (val: string | number | undefined): number => {
      if (typeof val === "number") return val;
      if (!val) return 0;
      const parsed = parseFloat(String(val));

      return isNaN(parsed) ? 0 : parsed;
    };

    return {
      accountId: data.account_number || data.account_id || "",
      status: data.is_sandbox ? "SANDBOX" : "ACTIVE",
      currency: "USD",
      buyingPower: parseFloatSafe(
        data.buying_power || data.net_liquidating_value,
      ),
      cash: parseFloatSafe(data.cash_balance || data.cash),
      portfolioValue: parseFloatSafe(data.net_liquidating_value),
      equity: parseFloatSafe(data.net_liquidating_value),
      longMarketValue: 0,
      shortMarketValue: 0,
      patternDayTrader: false,
      dayTradesRemaining: 0,
    };
  }

  async getPositions(accountId?: string | null, includeClosed: boolean = false): Promise<Position[]> {
    // Use TastyTrade HTTP API at tasty.gammabox.app
    console.log(`[API] 🔍 getPositions: Fetching ${includeClosed ? 'all' : 'open'} positions via TastyTrade HTTP API...`);
    
    try {
      const apiKey = this.tastyTradeHttpApiKey;
      if (!apiKey) {
        throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
      }

      // Get account ID if not provided (required for positions endpoint)
      const finalAccountId = accountId !== undefined ? accountId : await this.getAccountId();
      
      if (!finalAccountId) {
        throw new Error("account_id is required. Provide it as a parameter or configure it in your API key settings.");
      }

      // Build URL with account_id parameter (required)
      const url = `${this.tastyTradeHttpApiUrl}/api/v1/positions?account_id=${finalAccountId}`;
      
      console.log(`[API] 📡 Using TastyTrade HTTP API: ${url}`);
      
      const response = await fetch(url, {
        method: "GET",
        headers: {
          "X-API-Key": apiKey,
          "Content-Type": "application/json",
        },
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => response.statusText);
        let errorData;
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { detail: errorText };
        }
        
        // Extract error message from various possible fields
        const errorMsg = errorData.detail || errorData.error || errorData.message || errorText || `HTTP ${response.status}: ${response.statusText}`;
        // Provide helpful error message for credential registration
        if (errorMsg.includes("No credentials configured") || errorMsg.includes("Invalid API key")) {
          throw new Error(
            `${errorMsg}. Please register your TastyTrade credentials using: ` +
            `curl -X POST ${this.tastyTradeHttpApiUrl}/api/v1/credentials -H "Content-Type: application/json" ` +
            `-d '{"api_key": "${apiKey}", "client_secret": "YOUR_CLIENT_SECRET", "refresh_token": "YOUR_REFRESH_TOKEN"}' ` +
            `See docs/curl_instructions.md for details.`
          );
        }
        
        throw new Error(errorMsg);
      }

      const data = await response.json();
      const openPositions = data.positions || [];
      
      // Transform open positions
      const positions: Position[] = openPositions.map((pos: any) => {
        const quantity = parseFloat(pos.quantity || "0");
        const currentPrice = parseFloat(pos.current_price || pos.mark || "0");
        const avgPrice = parseFloat(pos.average_open_price || pos.average_fill_price || "0");
        
        // Calculate market value (current price * quantity * 100 for options, or just price * quantity for stocks)
        // For options, quantity is in contracts, so multiply by 100
        const isOption = pos.symbol?.includes(" ") || false;
        const multiplier = isOption ? 100 : 1;
        const marketValue = currentPrice * Math.abs(quantity) * multiplier;
        
        return {
          symbol: pos.symbol || "",
          quantity: Math.abs(quantity),
          side: quantity >= 0 ? "long" : "short",
          marketValue: marketValue,
          unrealizedPnl: parseFloat(pos.unrealized_pnl || pos.unrealized_fees || "0"),
          unrealizedPnlPercent: avgPrice > 0 ? (parseFloat(pos.unrealized_pnl || "0") / (avgPrice * Math.abs(quantity) * multiplier)) * 100 : 0,
          avgEntryPrice: avgPrice,
          dayPnl: parseFloat(pos.day_pnl || "0"),
          dayPnlPercent: parseFloat(pos.day_pnl_percent || "0"),
          status: "open" as const,
        };
      });
      
      // If including closed positions, fetch from transaction history
      if (includeClosed) {
        try {
          const transactionHistory = await this.getTransactionHistory(finalAccountId, { 
            days: 90,
            transactionType: "Trade"
          });
          
          // Extract closed positions from transaction history
          // Closed positions are trades where quantity becomes 0 or opposite action
          const closedTrades = transactionHistory.transactions.filter((tx: any) => {
            // Filter for trades that close positions (opposite action or quantity reduction)
            return tx.transaction_type === "Trade" && 
                   (tx.action?.includes("Close") || tx.action?.includes("close"));
          });
          
          // Group closed trades by symbol to calculate realized P&L
          const closedBySymbol: Record<string, any[]> = {};
          closedTrades.forEach((tx: any) => {
            const symbol = tx.symbol || tx.underlying_symbol;
            if (symbol) {
              if (!closedBySymbol[symbol]) {
                closedBySymbol[symbol] = [];
              }
              closedBySymbol[symbol].push(tx);
            }
          });
          
          // Create closed position entries
          Object.entries(closedBySymbol).forEach(([symbol, trades]) => {
            // Calculate total realized P&L from closed trades
            const totalRealizedPnl = trades.reduce((sum, tx) => {
              return sum + parseFloat(tx.fees || "0") + (parseFloat(tx.realized_pnl || "0"));
            }, 0);
            
            // Get the most recent close date
            const closeDate = trades
              .map((tx: any) => tx.executed_at || tx.transaction_date || tx.created_at)
              .filter(Boolean)
              .sort()
              .pop();
            
            // Find if there's an open position for this symbol
            const existingIndex = positions.findIndex(p => p.symbol === symbol);
            
            if (existingIndex >= 0) {
              // Update existing position with closed trade info
              positions[existingIndex].realizedPnl = totalRealizedPnl;
              positions[existingIndex].closeDate = closeDate;
            } else {
              // Add closed position entry
              const lastTrade = trades[trades.length - 1];
              const quantity = Math.abs(parseFloat(lastTrade.quantity || "0"));
              const avgPrice = parseFloat(lastTrade.average_fill_price || lastTrade.price || "0");
              
              positions.push({
                symbol: symbol,
                quantity: quantity,
                side: "long" as const, // Default, could be determined from trade action
                marketValue: 0, // Closed positions have no market value
                unrealizedPnl: 0,
                unrealizedPnlPercent: 0,
                avgEntryPrice: avgPrice,
                dayPnl: 0,
                dayPnlPercent: 0,
                status: "closed" as const,
                realizedPnl: totalRealizedPnl,
                realizedPnlPercent: avgPrice > 0 ? (totalRealizedPnl / (avgPrice * quantity * (symbol.includes(" ") ? 100 : 1))) * 100 : 0,
                closeDate: closeDate,
              });
            }
          });
        } catch (error) {
          console.warn("[API] Failed to fetch closed positions from transaction history:", error);
          // Continue with just open positions if closed positions fail
        }
      }
      
      return positions;
    } catch (error: any) {
      console.error("[API] getPositions error:", error);
      throw error;
    }
  }

  // Trading endpoints
  async analyzeMarket(
    symbol: string = "SPY",
    fastMode: boolean = true,
  ): Promise<TradeDecision> {
    const response = await this.request<{
      status: string;
      decision: TradeDecision;
    }>("/api/trading/analyze", {
      method: "POST",
      body: JSON.stringify({ symbol, fastMode }),
    });

    return response.decision;
  }

  async executeTrade(
    decision: TradeDecision,
    quantity?: number,
  ): Promise<TradeExecutionResult> {
    const response = await this.request<{ status: string; result: any }>(
      "/api/trading/execute",
      {
        method: "POST",
        body: JSON.stringify({ decision, quantity }),
      },
    );

    if (response.status === "ok" && response.result?.success) {
      // Handle multi-account execution results
      const result = response.result;

      // If multiple accounts were executed, show summary
      if (result.accounts_executed && result.accounts_executed > 1) {
        const successCount = result.accounts_successful || 0;
        const failCount = result.accounts_failed || 0;
        const mode = result.mode || "paper";

        // Return first successful result for backward compatibility
        const firstSuccess = result.results?.find((r: any) => r.success);

        return {
          success: true,
          orderId: firstSuccess?.orderId || result.orderId,
          symbol:
            result.symbol ||
            (decision.direction ? `SPY ${decision.direction}` : undefined),
          quantity: result.quantity || quantity,
          price: undefined,
          totalCost: undefined,
          // Include multi-account info
          multiAccount: {
            mode,
            total: result.accounts_executed,
            successful: successCount,
            failed: failCount,
            results: result.results,
          },
        };
      }

      // Single account execution (backward compatibility)
      const singleResult = result.result || result;

      return {
        success: true,
        orderId:
          singleResult?.orderId || singleResult?.order_id || singleResult?.id,
        symbol:
          result.symbol ||
          singleResult?.symbol ||
          (decision.direction ? `SPY ${decision.direction}` : undefined),
        quantity: result.quantity || singleResult?.quantity || quantity,
        price: singleResult?.price,
        totalCost: undefined,
      };
    }

    return {
      success: false,
      error:
        response.result?.error ||
        response.result?.message ||
        "Trade execution failed",
    };
  }

  async closePosition(
    symbol: string,
    qty?: number,
    percentage?: number,
  ): Promise<any> {
    return this.request(`/api/trading/close/${symbol}`, {
      method: "POST",
      body: JSON.stringify({ qty, percentage }),
    });
  }

  async getTradeHistory(accountId?: string | null, days: number = 90): Promise<any[]> {
    // Use TastyTrade HTTP API to get transaction history
    console.log(`[API] 🔍 getTradeHistory: Fetching transaction history via TastyTrade HTTP API...`);
    
    try {
      const apiKey = this.tastyTradeHttpApiKey;
      if (!apiKey) {
        throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
      }

      // Get account ID if not provided
      const finalAccountId = accountId !== undefined ? accountId : await this.getAccountId();
      
      // Use the transaction history endpoint
      const result = await this.getTransactionHistory(finalAccountId, { days });
      return result.transactions || [];
    } catch (error: any) {
      console.error("[API] getTradeHistory error:", error);
      throw error;
    }
  }

  async getTodayPnl(): Promise<TodayPnl> {
    // Calculate today's P&L from positions (unrealized) and transaction history (realized)
    // NOTE: This method intentionally does NOT call getAccountInfo() to avoid circular dependencies
    // The account info is fetched separately in the UI layer
    try {
      const accountId = await this.getAccountId();
      const [positions, transactions] = await Promise.all([
        this.getPositions(accountId),
        this.getTransactionHistory(accountId, {
          days: 1, // Just today
          transactionType: "Trade",
        }).catch(() => ({ transactions: [] })), // Fallback if history fails
      ]);

      // Calculate unrealized P&L from positions
      const unrealized = positions.reduce((sum, pos) => sum + (pos.unrealizedPnl || 0), 0);

      // Calculate realized P&L from today's transactions
      const today = new Date().toISOString().split("T")[0];
      const todayTransactions = transactions.transactions?.filter((tx: any) => {
        const txDate = tx.executed_at || tx.transaction_date || tx.created_at || "";
        return txDate.startsWith(today) && tx.transaction_type === "Trade";
      }) || [];

      // Sum up realized P&L from transactions (assuming they have a pnl field)
      const realized = todayTransactions.reduce((sum: number, tx: any) => {
        return sum + (parseFloat(tx.realized_pnl || tx.pnl || "0"));
      }, 0);

      // Don't fetch account info here to avoid circular calls - it's fetched separately
      // currentNetLiq can be set by the caller if needed

      return {
        realized,
        unrealized,
        total: realized + unrealized,
        currentNetLiq: null, // Set to null to avoid extra API call
        date: today,
      };
    } catch (error: any) {
      console.error("[API] getTodayPnl error:", error);
      // Return zero P&L on error
      const today = new Date().toISOString().split("T")[0];
      return {
        realized: 0,
        unrealized: 0,
        total: 0,
        currentNetLiq: null,
        date: today,
      };
    }
  }

  async getTodayTradeStats(): Promise<{
    open: number;
    closed: number;
    total: number;
  }> {
    try {
      // Use TastyTrade HTTP API endpoints
      const accountId = await this.getAccountId();
      const [positions, transactionsResult] = await Promise.all([
        this.getPositions(accountId),
        this.getTransactionHistory(accountId, {
          days: 1, // Just today
          transactionType: "Trade",
        }).catch(() => ({ transactions: [] })), // Fallback if history fails
      ]);

      // Count open positions
      const openCount = positions.length;

      // Count closed trades today
      const today = new Date().toISOString().split("T")[0]; // YYYY-MM-DD
      const todayTransactions = transactionsResult.transactions || [];

      // Count transactions that represent closed positions (e.g., sells/closes)
      const closedCount = todayTransactions.filter((tx: any) => {
        const txDate = tx.executed_at || tx.transaction_date || tx.created_at || "";
        // Count trades that are closing positions (sell actions or close indicators)
        return (
          txDate.startsWith(today) &&
          (tx.action?.toLowerCase().includes("sell") ||
            tx.action?.toLowerCase().includes("close") ||
            tx.transaction_type === "Trade")
        );
      }).length;

      return {
        open: openCount,
        closed: closedCount,
        total: openCount + closedCount,
      };
    } catch (error) {
      console.error("Failed to get trade stats:", error);

      return { open: 0, closed: 0, total: 0 };
    }
  }

  // Market data endpoints
  async getMarketQuote(symbol: string): Promise<any> {
    return this.request(`/api/market/quote/${symbol}`);
  }

  async getMarketSnapshot(symbol: string): Promise<{
    price: number;
    change: number;
    changePercent: number;
    bid: number;
    ask: number;
    volume: number;
    vwap: number;
    high: number;
    low: number;
    prevClose?: number;
    prevHigh?: number;
    prevLow?: number;
  }> {
    try {
      const response = await this.request<{
        status: string;
        [key: string]: any;
      }>(`/api/market/snapshot/${symbol}`);

      // If API returned an error status, throw
      if (response.status === "error") {
        throw new Error(response.error || "Failed to get market snapshot");
      }

      // Only return zeros if price is explicitly 0 (market closed, etc), otherwise throw if missing
      const price = response.price;

      if (price === undefined || price === null) {
        throw new Error("Market snapshot missing price data");
      }

      return {
        price: price || 0,
        change: response.change || 0,
        changePercent: response.changePercent || 0,
        bid: response.bid || 0,
        ask: response.ask || 0,
        volume: response.volume || 0,
        vwap: response.vwap || 0,
        high: response.high || 0,
        low: response.low || 0,
        prevClose: (response as any).prevClose,
        prevHigh: (response as any).prevHigh,
        prevLow: (response as any).prevLow,
      };
    } catch (error: any) {
      // Handle 421 Misdirected Request and other connection errors gracefully
      if (
        error.message?.includes("421") ||
        error.message?.includes("Misdirected") ||
        error.message?.includes("503") ||
        error.message?.includes("500")
      ) {
        console.warn(
          `[API] Market snapshot unavailable for ${symbol}, using fallback values`,
        );

        // Return zero values as fallback - UI should handle this gracefully
        return {
          price: 0,
          change: 0,
          changePercent: 0,
          bid: 0,
          ask: 0,
          volume: 0,
          vwap: 0,
          high: 0,
          low: 0,
        };
      }
      throw error;
    }
  }

  // Chat endpoint (non-streaming, for backwards compatibility)
  // Now uses TastyTrade HTTP API to match backend
  async sendChatMessage(
    message: string,
    context?: any,
    images?: string[],
  ): Promise<{ response: string }> {
    // Convert context to message_history format if needed
    let messageHistory: Array<{ role: "user" | "assistant"; content: string }> | undefined;
    
    if (context) {
      // If context is already in the correct format (array of {role, content})
      if (Array.isArray(context) && context.length > 0 && typeof context[0] === 'object' && 'role' in context[0]) {
        messageHistory = context as Array<{ role: "user" | "assistant"; content: string }>;
      } else if (Array.isArray(context)) {
        // If context is an array of messages, try to convert
        messageHistory = context.map((msg: any) => ({
          role: (msg.role || (msg.role === 'user' ? 'user' : 'assistant')) as "user" | "assistant",
          content: msg.content || msg.message || String(msg)
        }));
      }
    }
    
    // Use TastyTrade HTTP API instead of internal API
    const result = await this.sendTastyTradeChatMessage(message, messageHistory, images);
    return { response: result.response };
  }

  // TastyTrade HTTP API Chat endpoint (from curl_instructions.md)
  // Uses the external TastyTrade HTTP API service at https://tasty.gammabox.app
  async sendTastyTradeChatMessage(
    message: string,
    messageHistory?: Array<{ role: "user" | "assistant"; content: string }>,
    images?: string[],
  ): Promise<{ response: string; message_history: Array<{ role: string; content: string }> }> {
    const apiKey = this.tastyTradeHttpApiKey;
    
    if (!apiKey) {
      throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
    }

    const url = `${this.tastyTradeHttpApiUrl}/api/v1/chat`;
    
    // Build request body matching backend ChatRequest model
    const requestBody: {
      message: string;
      message_history?: Array<{ role: string; content: string }> | null;
      images?: string[] | null;
    } = {
      message,
    };
    
    if (messageHistory && messageHistory.length > 0) {
      requestBody.message_history = messageHistory;
    } else {
      requestBody.message_history = null;
    }
    
    if (images && images.length > 0) {
      requestBody.images = images;
    } else {
      requestBody.images = null;
    }
    
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => response.statusText);
        let errorData;
        
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { detail: errorText };
        }

        // Extract error message from various possible fields
        const errorMessage = 
          errorData.detail || 
          errorData.error || 
          errorData.message || 
          errorText || 
          `HTTP ${response.status}: ${response.statusText}`;

        throw new Error(errorMessage);
      }

      const data = await response.json();
      return {
        response: data.response || "",
        message_history: data.message_history || [],
      };
    } catch (error: any) {
      console.error("[API] TastyTrade HTTP API chat error:", error);
      
      // Extract meaningful error message
      let errorMessage = "Unknown error";
      if (error instanceof Error) {
        errorMessage = error.message;
      } else if (typeof error === "string") {
        errorMessage = error;
      } else if (error?.message) {
        errorMessage = error.message;
      } else if (error?.detail) {
        errorMessage = error.detail;
      } else if (error?.error) {
        errorMessage = error.error;
      }
      
      // Include additional context for network errors
      if (errorMessage === "Unknown error" || errorMessage.includes("fetch") || errorMessage.includes("Failed to fetch")) {
        errorMessage = `Failed to connect to chat API at ${url}. ${errorMessage}. Please check your network connection and API endpoint configuration.`;
      }
      
      throw new Error(errorMessage);
    }
  }

  // TastyTrade HTTP API Streaming Chat endpoint
  // Streams responses from the Claude agent using Server-Sent Events (SSE)
  async *streamTastyTradeChatMessage(
    message: string,
    messageHistory?: Array<{ role: "user" | "assistant"; content: string }>,
    onChunk?: (chunk: string) => void,
    images?: string[],
  ): AsyncGenerator<
    {
      type: "text" | "status" | "done" | "error" | "tool_use";
      content?: string;
      error?: string;
      tool_name?: string;
      elapsed_time?: string;
    },
    void,
    unknown
  > {
    const apiKey = this.tastyTradeHttpApiKey;
    
    if (!apiKey) {
      throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
    }

    const url = `${this.tastyTradeHttpApiUrl}/api/v1/chat/stream`;
    
    let response: Response;

    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({
          message,
          message_history: messageHistory || null,
          images: images && images.length > 0 ? images : null,
        }),
      });
    } catch (fetchError: any) {
      console.error("[API] Fetch error in streamTastyTradeChatMessage:", fetchError);
      const errorMsg = fetchError?.message || fetchError?.error || String(fetchError) || "Network error";
      throw new Error(
        `Failed to connect to chat API at ${url}: ${errorMsg}`
      );
    }

    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}: ${response.statusText}`;

      try {
        const errorText = await response.text().catch(() => response.statusText);
        let errorData;
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { detail: errorText };
        }
        errorMessage = errorData.detail || errorData.error || errorData.message || errorText || errorMessage;
      } catch {
        // Already have default errorMessage
      }
      console.error("[API] Chat stream error:", errorMessage);
      throw new Error(errorMessage);
    }

    const reader = response.body?.getReader();

    if (!reader) {
      throw new Error("Response body is not readable");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          // Process any remaining buffer before breaking
          if (buffer.trim()) {
            const lines = buffer.split("\n");

            for (const line of lines) {
              if (line.trim() && line.startsWith("data: ")) {
                try {
                  const data = JSON.parse(line.slice(6));

                  if (data.type === "text" && data.content) {
                    if (onChunk) {
                      onChunk(data.content);
                    }
                  }
                  yield data;
                } catch (e) {
                  console.error("Failed to parse final SSE data:", e, line);
                }
              }
            }
          }
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");

        buffer = lines.pop() || ""; // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.trim() && line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));

              if (data.type === "text" && data.content) {
                if (onChunk) {
                  onChunk(data.content);
                }
              }
              yield data;
            } catch (e) {
              console.error("Failed to parse SSE data:", e, line);
            }
          }
        }
      }
    } catch (streamError: any) {
      // Handle errors during streaming
      const errorMessage = streamError?.message || streamError?.error || String(streamError) || "Stream interrupted";
      console.error("[API] Error during chat stream:", errorMessage, streamError);
      // Yield error chunk - the chat interface will handle it and throw appropriately
      yield { type: "error", error: errorMessage };
    } finally {
      reader.releaseLock();
    }
  }

  // Usage tracking endpoints
  async getUsageSummary(userId: string = "default"): Promise<UsageSummary> {
    try {
      const response = await this.request<{
        status: string;
        data: UsageSummary;
      }>(`/api/usage/${userId}`);

      return response.data;
    } catch (error: any) {
      // If API endpoint doesn't exist (404), return default free tier usage
      if (
        error.message?.includes("404") ||
        error.message?.includes("Cannot GET")
      ) {
        console.warn(
          "[API] Usage endpoint not available, using default free tier",
        );

        return {
          userId,
          tier: "free",
          billingCycleStart: new Date().toISOString().split("T")[0],
          usage: {
            chat_message: { used: 0, limit: 10, remaining: 10 },
            trade_analysis: { used: 0, limit: 5, remaining: 5 },
            trade_execution: { used: 0, limit: 0, remaining: 0 },
          },
          isOverLimit: false,
        };
      }
      throw error;
    }
  }

  async checkUsage(
    userId: string = "default",
    actionType: string,
  ): Promise<UsageCheckResult> {
    try {
      const response = await this.request<{
        status: string;
        data: UsageCheckResult;
      }>(`/api/usage/${userId}/check/${actionType}`);

      return response.data;
    } catch (error: any) {
      // If API endpoint doesn't exist (404), allow the action (kiosk mode)
      if (
        error.message?.includes("404") ||
        error.message?.includes("Cannot GET")
      ) {
        console.warn(
          "[API] Usage check endpoint not available, allowing action",
        );

        return {
          allowed: true,
          remaining: -1,
          limit: -1,
        };
      }
      throw error;
    }
  }

  // Generate dynamic chips
  async generateChips(
    conversationHistory?: Array<{ role: string; content: string }>,
  ): Promise<{
    chips: Array<{ title: string; description: string }>;
    marketPhase: string;
    marketStatus: { isOpen: boolean; currentTime: string };
  }> {
    const response = await this.request<{
      status: string;
      chips: Array<{ title: string; description: string }>;
      marketPhase: string;
      marketStatus: { isOpen: boolean; currentTime: string };
    }>("/api/chat/chips/generate", {
      method: "POST",
      body: JSON.stringify({ conversationHistory }),
    });

    return {
      chips: response.chips,
      marketPhase: response.marketPhase,
      marketStatus: response.marketStatus,
    };
  }

  // Account management endpoints
  async getAccounts(): Promise<TastyTradeAccount[]> {
    const response = await this.request<{
      status: string;
      accounts: TastyTradeAccount[];
    }>("/api/accounts");

    return response.accounts;
  }

  async getEnabledAccounts(): Promise<{
    accounts: TastyTradeAccount[];
  }> {
    const response = await this.request<{
      status: string;
      accounts: TastyTradeAccount[];
    }>("/api/accounts/enabled");

    return { accounts: response.accounts };
  }

  async createAccount(data: {
    account_name: string;
    account_id: string;
    client_secret: string;
    refresh_token: string;
    is_sandbox?: boolean;
    created_by?: string;
  }): Promise<TastyTradeAccount> {
    const response = await this.request<{
      status: string;
      account: TastyTradeAccount;
    }>("/api/accounts", {
      method: "POST",
      body: JSON.stringify(data),
    });

    return response.account;
  }

  async updateAccount(
    id: number,
    data: Partial<{
      account_name: string;
      account_id: string;
      client_secret: string;
      refresh_token: string;
      is_enabled: boolean;
    }>,
  ): Promise<TastyTradeAccount> {
    const response = await this.request<{
      status: string;
      account: TastyTradeAccount;
    }>(`/api/accounts/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });

    return response.account;
  }

  async deleteAccount(id: number): Promise<void> {
    await this.request<{ status: string; message: string }>(
      `/api/accounts/${id}`,
      {
        method: "DELETE",
      },
    );
  }

  async toggleAccount(id: number): Promise<TastyTradeAccount> {
    const response = await this.request<{
      status: string;
      account: TastyTradeAccount;
    }>(`/api/accounts/${id}/toggle`, {
      method: "POST",
    });

    return response.account;
  }

  async setDefaultAccount(id: number): Promise<TastyTradeAccount> {
    const response = await this.request<{
      status: string;
      account: TastyTradeAccount;
      message: string;
    }>(`/api/accounts/${id}/default`, {
      method: "POST",
    });

    return response.account;
  }

  // Setup endpoints
  async getSetupStatus(): Promise<{
    isSetupComplete: boolean;
    deviceId?: string;
    hasTailscale?: boolean;
    tailscaleConnected?: boolean;
    dockerConfigured?: boolean;
    missingFields?: string[];
  }> {
    const response = await this.request<any>("/api/setup/status");

    // Handle both old and new response formats
    if (response.tastytrade) {
      // Old format - convert to new format
      return {
        isSetupComplete: false, // Old format doesn't have this, default to false
        deviceId: undefined,
        hasTailscale: false,
        tailscaleConnected: false,
        dockerConfigured: false,
        missingFields: ["DEVICE_ID"],
      };
    }

    // New format - return as-is
    return {
      isSetupComplete: response.isSetupComplete ?? false,
      deviceId: response.deviceId,
      hasTailscale: response.hasTailscale,
      tailscaleConnected: response.tailscaleConnected,
      dockerConfigured: response.dockerConfigured,
      missingFields: response.missingFields || [],
    };
  }

  async saveSetup(data: {
    deviceId: string;
    tailscaleAuthKey?: string;
    apiUrl?: string;
    wsUrl?: string;
  }): Promise<{ success: boolean; message?: string }> {
    const response = await this.request<{ success: boolean; message?: string }>(
      "/api/setup/save",
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    );

    return response;
  }

  async getTailscaleStatus(): Promise<{
    connected: boolean;
    ip?: string;
    hostname?: string;
  }> {
    const response = await this.request<{
      connected: boolean;
      ip?: string;
      hostname?: string;
    }>("/api/setup/tailscale/status");

    return response;
  }

  async restartServices(): Promise<{ success: boolean; message?: string }> {
    const response = await this.request<{ success: boolean; message?: string }>(
      "/api/setup/restart",
      {
        method: "POST",
      },
    );

    return response;
  }

  // Streaming chat endpoint
  // Uses TastyTrade HTTP API to match backend structure
  async *streamChatMessage(
    message: string,
    context?: any,
    onChunk?: (chunk: string) => void,
    images?: string[],
  ): AsyncGenerator<
    {
      type: "text" | "status" | "done" | "error" | "tool_use";
      content?: string;
      error?: string;
      message?: string;
      tokenUsage?: any;
      elapsed_time?: string;
    },
    void,
    unknown
  > {
    // Convert context to message_history format if needed
    let messageHistory: Array<{ role: "user" | "assistant"; content: string }> | undefined;
    
    if (context) {
      // If context is already in the correct format (array of {role, content})
      if (Array.isArray(context) && context.length > 0 && typeof context[0] === 'object' && 'role' in context[0]) {
        messageHistory = context as Array<{ role: "user" | "assistant"; content: string }>;
      } else if (Array.isArray(context)) {
        // If context is an array of messages, try to convert
        messageHistory = context.map((msg: any) => ({
          role: (msg.role || (msg.role === 'user' ? 'user' : 'assistant')) as "user" | "assistant",
          content: msg.content || msg.message || String(msg)
        }));
      }
    }

    let response: Response;
    
    // Use TastyTrade HTTP API for streaming chat
    const url = `${this.tastyTradeHttpApiUrl}/api/v1/chat/stream`;
    const apiKey = this.tastyTradeHttpApiKey;

    try {
      
      if (!apiKey) {
        throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
      }
      
      // Build request body matching backend ChatRequest model
      const requestBody: {
        message: string;
        message_history?: Array<{ role: string; content: string }> | null;
        images?: string[] | null;
      } = {
        message,
      };
      
      if (messageHistory && messageHistory.length > 0) {
        requestBody.message_history = messageHistory;
      } else {
        requestBody.message_history = null;
      }
      
      if (images && images.length > 0) {
        requestBody.images = images;
      } else {
        requestBody.images = null;
      }
      
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify(requestBody),
      });
    } catch (fetchError: any) {
      console.error("[API] Fetch error in streamChatMessage:", fetchError);
      const errorMsg = fetchError?.message || fetchError?.error || String(fetchError) || "Network error";
      throw new Error(
        `Failed to connect to chat API at ${url}: ${errorMsg}`,
      );
    }

    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}: ${response.statusText}`;

      try {
        const errorText = await response.text().catch(() => response.statusText);
        let errorData;
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { detail: errorText };
        }
        errorMessage = errorData.detail || errorData.error || errorData.message || errorText || errorMessage;
      } catch {
        // Already have default errorMessage
      }
      console.error("[API] Chat stream error:", errorMessage);
      throw new Error(errorMessage);
    }

    const reader = response.body?.getReader();

    if (!reader) {
      throw new Error("Response body is not readable");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          // Process any remaining buffer before breaking
          if (buffer.trim()) {
            const lines = buffer.split("\n");

            for (const line of lines) {
              if (line.trim() && line.startsWith("data: ")) {
                try {
                  const data = JSON.parse(line.slice(6));

                  // Handle different event types
                  if (data.type === "status") {
                    // Status updates (e.g., "Processing request...", "Analyzing...")
                    // These provide immediate feedback while agent is thinking
                    console.log("[API] Status:", data.content);
                  } else if (data.type === "text" && data.content) {
                    // Text chunks - stream to onChunk callback
                    if (onChunk) {
                      onChunk(data.content);
                    }
                  } else if (data.type === "done") {
                    console.log("[API] Stream complete", data.elapsed_time ? `(${data.elapsed_time})` : "");
                  }
                  yield data;
                } catch (e) {
                  console.error("Failed to parse final SSE data:", e, line);
                }
              }
            }
          }
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");

        buffer = lines.pop() || ""; // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.trim() && line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));

              // Handle different event types
              if (data.type === "status") {
                // Status updates provide immediate feedback
                console.log("[API] Status:", data.content);
              } else if (data.type === "text" && data.content) {
                // Text chunks - stream to onChunk callback
                if (onChunk) {
                  onChunk(data.content);
                }
              } else if (data.type === "done") {
                console.log("[API] Stream complete", data.elapsed_time ? `(${data.elapsed_time})` : "");
              } else if (data.type === "tool_use") {
                console.log("[API] Tool use:", data);
              }
              yield data;
            } catch (e) {
              console.error("Failed to parse SSE data:", e, line);
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  // ============================================================================
  // TastyTrade HTTP API Endpoints (from curl_REST_instruction.md)
  // ============================================================================

  // Helper method to build URL with optional account_id
  private buildTastyTradeUrl(endpoint: string, accountId?: string | null, params?: Record<string, string>): string {
    const baseUrl = `${this.tastyTradeHttpApiUrl}${endpoint}`;
    const urlParams = new URLSearchParams();
    
    if (accountId) {
      urlParams.append("account_id", accountId);
    }
    
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          urlParams.append(key, value);
        }
      });
    }
    
    const queryString = urlParams.toString();
    return queryString ? `${baseUrl}?${queryString}` : baseUrl;
  }

  // Helper method for TastyTrade HTTP API requests
  private async tastyTradeRequest<T>(
    endpoint: string,
    options?: {
      method?: "GET" | "POST" | "PUT" | "DELETE";
      accountId?: string | null;
      params?: Record<string, string>;
      body?: any;
    }
  ): Promise<T> {
    const apiKey = this.tastyTradeHttpApiKey;
    if (!apiKey) {
      throw new Error("TastyTrade HTTP API key not configured. Set NEXT_PUBLIC_TASTY_HTTP_API_KEY environment variable.");
    }

    const accountId = options?.accountId !== undefined ? options.accountId : await this.getAccountId();
    const url = this.buildTastyTradeUrl(endpoint, accountId, options?.params);

    const response = await fetch(url, {
      method: options?.method || "GET",
      headers: {
        "X-API-Key": apiKey,
        "Content-Type": "application/json",
      },
      body: options?.body ? JSON.stringify(options.body) : undefined,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => response.statusText);
      let errorData;
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = { detail: errorText };
      }
      // Extract error message from various possible fields
      const errorMessage = 
        errorData.detail || 
        errorData.error || 
        errorData.message || 
        errorText || 
        `HTTP ${response.status}: ${response.statusText}`;
      throw new Error(errorMessage);
    }

    return response.json();
  }

  // Account & Portfolio Endpoints
  async getNetLiquidatingValueHistory(
    accountId?: string | null,
    timeBack: "1d" | "1m" | "3m" | "6m" | "1y" | "all" = "1y"
  ): Promise<{ history: any[]; table: string }> {
    return this.tastyTradeRequest("/api/v1/net-liquidating-value-history", {
      accountId,
      params: { time_back: timeBack },
    });
  }

  async getTransactionHistory(
    accountId?: string | null,
    options?: {
      days?: number;
      underlyingSymbol?: string;
      transactionType?: "Trade" | "Money Movement";
    }
  ): Promise<{ transactions: any[]; table: string }> {
    // Get account ID if not provided
    const finalAccountId = accountId !== undefined ? accountId : await this.getAccountId();
    
    const params: Record<string, string> = {};
    if (options?.days) params.days = String(options.days);
    if (options?.underlyingSymbol) params.underlying_symbol = options.underlyingSymbol;
    if (options?.transactionType) params.transaction_type = options.transactionType;

    return this.tastyTradeRequest("/api/v1/transaction-history", {
      accountId: finalAccountId,
      params,
    });
  }

  async getOrderHistory(
    accountId?: string | null,
    options?: {
      days?: number;
      underlyingSymbol?: string;
    }
  ): Promise<{ orders: any[]; table: string }> {
    // Get account ID if not provided
    const finalAccountId = accountId !== undefined ? accountId : await this.getAccountId();
    
    const params: Record<string, string> = {};
    if (options?.days) params.days = String(options.days);
    if (options?.underlyingSymbol) params.underlying_symbol = options.underlyingSymbol;

    return this.tastyTradeRequest("/api/v1/order-history", {
      accountId: finalAccountId,
      params,
    });
  }

  // Market Data Endpoints
  async getQuotes(
    instruments: Array<{
      symbol: string;
      option_type?: "C" | "P";
      strike_price?: number;
      expiration_date?: string;
    }>,
    timeout: number = 10.0
  ): Promise<{ quotes: any[]; table: string; claude_analysis?: string }> {
    return this.tastyTradeRequest("/api/v1/quotes", {
      method: "POST",
      params: { timeout: String(timeout) },
      body: instruments,
    });
  }

  async getGreeks(
    options: Array<{
      symbol: string;
      option_type: "C" | "P";
      strike_price: number;
      expiration_date: string;
    }>,
    timeout: number = 10.0
  ): Promise<{ greeks: any[]; table: string; claude_analysis?: string }> {
    return this.tastyTradeRequest("/api/v1/greeks", {
      method: "POST",
      params: { timeout: String(timeout) },
      body: options,
    });
  }

  async getMarketMetrics(symbols: string[]): Promise<{ metrics: any[]; table: string; claude_analysis?: string }> {
    return this.tastyTradeRequest("/api/v1/market-metrics", {
      method: "POST",
      body: symbols,
    });
  }

  async getMarketStatus(exchanges?: ("Equity" | "CME" | "CFE" | "Smalls")[]): Promise<any[]> {
    const params: Record<string, string> = {};
    if (exchanges && exchanges.length > 0) {
      params.exchanges = exchanges.join(",");
    }

    return this.tastyTradeRequest("/api/v1/market-status", {
      params,
    });
  }

  async searchSymbols(symbol: string): Promise<{ results: any[]; table: string; claude_analysis?: string }> {
    return this.tastyTradeRequest("/api/v1/search-symbols", {
      params: { symbol },
    });
  }

  async getOptionChain(symbol: string): Promise<{ 
    symbol: string; 
    total_expirations: number;
    total_options: number;
    expiration_dates: string[]; 
    chain: Record<string, { 
      calls: Array<{ strike_price: number; option_type: string; streamer_symbol: string; expiration_date: string; symbol: string }>; 
      puts: Array<{ strike_price: number; option_type: string; streamer_symbol: string; expiration_date: string; symbol: string }>; 
      strikes: number[]; 
      total_options: number;
    }>; 
    all_options: Array<{ strike_price: number; option_type: string; streamer_symbol: string; expiration_date: string; symbol: string }>;
    table: string 
  }> {
    return this.tastyTradeRequest("/api/v1/option-chain", {
      params: { symbol },
    });
  }

  // Trading Endpoints
  async getLiveOrders(accountId?: string | null): Promise<{ orders: any[]; table: string }> {
    return this.tastyTradeRequest("/api/v1/live-orders", {
      accountId,
    });
  }

  async placeOrder(
    legs: Array<{
      symbol: string;
      action: string;
      quantity: number;
      option_type?: "C" | "P";
      strike_price?: number;
      expiration_date?: string;
    }>,
    accountId?: string | null,
    options?: {
      order_type?: "Market" | "Limit" | "Stop" | "StopLimit" | "TrailingStop";
      price?: number | null;
      stop_price?: number | null;
      trail_price?: number | null;
      trail_percent?: number | null;
      time_in_force?: "Day" | "GTC" | "IOC";
      dry_run?: boolean;
    }
  ): Promise<any> {
    return this.tastyTradeRequest("/api/v1/place-order", {
      method: "POST",
      accountId,
      body: {
        legs,
        order_type: options?.order_type || "Limit",
        price: options?.price ?? null,
        stop_price: options?.stop_price ?? null,
        trail_price: options?.trail_price ?? null,
        trail_percent: options?.trail_percent ?? null,
        time_in_force: options?.time_in_force || "Day",
        dry_run: options?.dry_run || false,
      },
    });
  }

  async replaceOrder(
    orderId: string,
    price: number,
    accountId?: string | null
  ): Promise<any> {
    return this.tastyTradeRequest(`/api/v1/replace-order/${orderId}`, {
      method: "POST",
      accountId,
      body: { price },
    });
  }

  async cancelOrder(orderId: string, accountId?: string | null): Promise<{ success: boolean; order_id: string }> {
    return this.tastyTradeRequest(`/api/v1/orders/${orderId}`, {
      method: "DELETE",
      accountId,
    });
  }

  // Watchlist Endpoints
  async getWatchlists(
    options?: {
      watchlistType?: "public" | "private";
      name?: string;
    }
  ): Promise<{ watchlists: any[]; claude_analysis?: string }> {
    const params: Record<string, string> = {};
    if (options?.watchlistType) params.watchlist_type = options.watchlistType;
    if (options?.name) params.name = options.name;

    return this.tastyTradeRequest("/api/v1/watchlists", {
      params,
    });
  }

  async managePrivateWatchlist(
    action: "add" | "remove",
    symbols: Array<{ symbol: string; instrument_type: string }>,
    name: string = "main"
  ): Promise<{ success: boolean; action: string; watchlist: string; symbols_count: number }> {
    return this.tastyTradeRequest("/api/v1/watchlists/private/manage", {
      method: "POST",
      body: {
        action,
        symbols,
        name,
      },
    });
  }

  async deletePrivateWatchlist(name: string): Promise<{ success: boolean; watchlist: string }> {
    return this.tastyTradeRequest(`/api/v1/watchlists/private/${name}`, {
      method: "DELETE",
    });
  }

  // Utility Endpoints
  async getCurrentTimeNYC(): Promise<{ current_time_nyc: string }> {
    return this.tastyTradeRequest("/api/v1/current-time");
  }

  async getHealth(): Promise<{ status: string; service: string }> {
    // Health endpoint doesn't require API key
    const response = await fetch(`${this.tastyTradeHttpApiUrl}/health`);
    if (!response.ok) {
      throw new Error(`Health check failed: ${response.statusText}`);
    }
    return response.json();
  }

  // Credentials Management Endpoints
  async addOrUpdateCredentials(data: {
    api_key: string;
    client_secret: string;
    refresh_token: string;
  }): Promise<{ success: boolean; api_key: string; message: string }> {
    return this.tastyTradeRequest("/api/v1/credentials", {
      method: "POST",
      body: data,
    });
  }

  async listCredentials(): Promise<{ api_keys: Array<{ api_key: string; configured: boolean }>; count: number }> {
    return this.tastyTradeRequest("/api/v1/credentials");
  }

  async deleteCredentials(apiKey: string): Promise<{ success: boolean; api_key: string; message: string }> {
    return this.tastyTradeRequest(`/api/v1/credentials/${apiKey}`, {
      method: "DELETE",
    });
  }
}

export interface TastyTradeAccount {
  id: number;
  account_name: string;
  account_id: string;
  is_sandbox: boolean;
  is_enabled: boolean;
  is_default: boolean;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export const apiClient = new ApiClient();
