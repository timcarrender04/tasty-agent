# 🚨 CRITICAL: Option Contract Cost Calculation

## The 100x Multiplier Error

**EVERY option contract represents 100 shares of the underlying stock.**

When calculating option contract costs, you **MUST multiply the premium per share by 100** to get the actual cost per contract.

---

## ❌ Common Error

**WRONG:**
- Premium: $10.05 per share
- Cost for 10 contracts: $10.05 × 10 = **$100.50** ❌
- This is **10x too small**!

## ✅ Correct Calculation

**CORRECT:**
- Premium: $10.05 per share
- Cost per contract: $10.05 × 100 = **$1,005.00**
- Cost for 10 contracts: $1,005.00 × 10 = **$10,050.00** ✅

---

## Formula

```
Cost per Contract = Premium per Share × 100
Total Cost = Cost per Contract × Number of Contracts
```

### Example: $240 Strike IWM Calls

**Given:**
- Current IWM price: $249.89
- Strike: $240
- Premium per share: $10.05
- Buying power: $14,882.03

**Calculation:**
1. Cost per contract = $10.05 × 100 = **$1,005.00**
2. Maximum affordable contracts = $14,882.03 ÷ $1,005.00 = **14 contracts** (not 148!)
3. Cost for 10 contracts = $1,005.00 × 10 = **$10,050.00** (uses 67.5% of buying power)

---

## Real-World Impact

### Error in Analysis
If an analysis shows:
- ❌ 10 contracts cost **$1,005**
- ❌ You can afford **148 contracts**
- ❌ P&L shows **+$202** for 10 contracts

**This is catastrophically wrong and would cause:**
- Massive over-leveraging
- Thinking you're risking $5,000 when actually risking $50,000
- Account margin calls or liquidation
- Complete capital destruction

### Correct Analysis
- ✅ 1 contract costs **$1,005**
- ✅ You can afford **14 contracts**
- ✅ 10 contracts cost **$10,050**
- ✅ P&L for 10 contracts: **+$1,950** (not +$202)

---

## Contract Cost Table Reference

| Premium per Share | Cost per Contract | 5 Contracts | 10 Contracts | 14 Contracts |
|------------------|-------------------|-------------|--------------|--------------|
| $10.05 | **$1,005** | $5,025 | $10,050 | $14,070 |
| $15.10 | **$1,510** | $7,550 | $15,100 | $21,140 |
| $15.40 | **$1,540** | $7,700 | $15,400 | $21,560 |

**Buying Power: $14,882.03**
- Max $240 calls (@ $10.05): **14 contracts**
- Max $235 calls (@ $15.10): **9 contracts**
- Max $265 puts (@ $15.40): **9 contracts**

---

## P&L Calculations

### Example: $240 Strike Calls

**Entry:**
- 10 contracts at $10.05/share
- Total cost: $10.05 × 100 × 10 = **$10,050**

**Exit Scenarios:**

**Win (+$1.95/share):**
- Exit price: $12.00/share
- Value per contract: $12.00 × 100 = $1,200
- Total value: $1,200 × 10 = $12,000
- **Profit: $12,000 - $10,050 = +$1,950** ✅
- Return: +19.4%

**Loss (-$2.52/share):**
- Exit price: $7.53/share
- Value per contract: $7.53 × 100 = $753
- Total value: $753 × 10 = $7,530
- **Loss: $7,530 - $10,050 = -$2,520** ✅
- Return: -25.1%

---

## Code Reference

The correct calculation is implemented in:
```typescript
// GammaBox_Kiosk_v2/backend_server/api/src/services/sonnet.ts
function calculatePositionSize(
  conviction: number,
  buyingPower: number,
  contractPrice: number, // This is the premium per share
): number {
  // ... conviction logic ...
  const positionValue = buyingPower * positionPercent;
  // ✅ CORRECT: Multiply by 100 for options
  const contractCost = contractPrice * 100;
  const quantity = Math.floor(positionValue / contractCost);
  return Math.max(1, Math.min(10, quantity));
}
```

---

## When Creating Option Analysis

**ALWAYS:**
1. ✅ Confirm premium is per share (from quote/chain data)
2. ✅ Multiply premium × 100 to get cost per contract
3. ✅ Calculate affordable contracts: Buying Power ÷ Cost per Contract
4. ✅ Calculate total cost: Cost per Contract × Number of Contracts
5. ✅ Calculate P&L: (Exit Premium - Entry Premium) × 100 × Contracts

**NEVER:**
1. ❌ Use premium directly as contract cost
2. ❌ Forget the 100x multiplier
3. ❌ Show affordable quantities without multiplying by 100
4. ❌ Calculate P&L without multiplying by 100

---

## Verification Checklist

Before presenting any option analysis:

- [ ] Premium per share is clearly labeled as "per share"
- [ ] Cost per contract = Premium × 100
- [ ] Maximum affordable contracts calculated correctly
- [ ] Total position cost calculated correctly
- [ ] P&L scenarios include 100x multiplier
- [ ] All dollar amounts are realistic given buying power
- [ ] Numbers are verified (e.g., if premium is $10.05, 10 contracts = $10,050, not $100.50)

---

## Summary

**THE RULE:**
> **One option contract = 100 shares = Premium × 100**

This is not optional. This is not a suggestion. This is **fundamental to options trading**. Getting this wrong will destroy accounts.

**Always multiply by 100. Always.**

