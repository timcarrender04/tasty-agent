# API Error Handling

## Overview

The `place_order` endpoint passes through **exact error messages from the TastyTrade API** without modification. This allows the frontend to receive and display the raw API errors for validation and debugging.

## Error Flow

```
TastyTrade API Error → FastAPI Exception → HTTP Response → Frontend
```

The error message from TastyTrade API is extracted and passed directly to the frontend without reformatting.

## Error Message Extraction

### For TastyApiError (if available):

1. **Primary Message**: Uses `e.message` attribute (structured error from API)
2. **Error Details**: Appends `e.errors` details if available (for debugging)
3. **Status Code**: Uses `e.status_code` if provided by API

### For Generic Exceptions:

- Uses `str(e)` to get the exception message
- Maps error patterns to appropriate HTTP status codes

## HTTP Response Format

When an error occurs, the frontend receives:

```json
{
  "detail": "Exact error message from TastyTrade API"
}
```

### Example: Market Closed

```json
{
  "detail": "Market is closed"
}
```

The `detail` field contains the **exact error message** from the TastyTrade API.

## Frontend Validation

The frontend component can validate that the API is working correctly by:

1. **Checking Error Message**: The error message comes directly from TastyTrade API
2. **Error Type Detection**: Frontend can parse error messages to determine error type
3. **User Feedback**: Display the exact API error to users for transparency

## Status Codes

- **400**: Client errors (market closed, validation errors, invalid requests)
- **500**: Server errors (unexpected API failures)

The status code is determined by:
1. TastyApiError's `status_code` attribute (if available)
2. Error message pattern matching
3. Default to 500 for unknown errors

## Logging

All errors are logged server-side with:
- Error message
- Status code
- Exception type
- Full traceback (for debugging)

This allows debugging without exposing internal details to the frontend.

## Testing

To test error handling:

1. **Market Closed**: Place an order when market is closed
   - Should return: `"Market is closed"` (exact API message)
   
2. **Invalid Order**: Place an order with invalid parameters
   - Should return: API's validation error message

3. **Frontend Validation**: Check that `response.detail` contains the exact API error

## Notes

- **No Message Modification**: We never reformat or change the API's error message
- **Direct Passthrough**: `detail` field contains exactly what TastyTrade API returns
- **Structured Errors**: If `TastyApiError` is available, we use its structured message
- **Fallback**: If structured error unavailable, we use `str(e)` to preserve message
