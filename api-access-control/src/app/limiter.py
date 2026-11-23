import calendar
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import redis.asyncio as redis

# Atomic Lua script for Token Bucket + Monthly Quota reservation
RATE_LIMIT_LUA_SCRIPT = """
local tb_key = KEYS[1]
local quota_key = KEYS[2]

local cost = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local refill_per_sec = tonumber(ARGV[3])
local monthly_allowance = tonumber(ARGV[4])
local seconds_until_next_month = tonumber(ARGV[5])

local time_res = redis.call('TIME')
local now = tonumber(time_res[1]) + (tonumber(time_res[2]) / 1000000)

-- 1. Check Monthly Quota
local current_quota = tonumber(redis.call('GET', quota_key) or '0')
if (current_quota + cost) > monthly_allowance then
    return {
        'QUOTA_EXHAUSTED',
        tostring(seconds_until_next_month),
        tostring(monthly_allowance),
        tostring(math.max(0, monthly_allowance - current_quota))
    }
end

-- 2. Check Token Bucket (short-term rate limit)
local tb = redis.call('HMGET', tb_key, 'tokens', 'last_ts')
local tokens = tonumber(tb[1])
local last_ts = tonumber(tb[2])

if tokens == nil or last_ts == nil then
    tokens = burst
    last_ts = now
else
    local delta = math.max(0, now - last_ts)
    tokens = math.min(burst, tokens + (delta * refill_per_sec))
    last_ts = now
end

if tokens < cost then
    local shortfall = cost - tokens
    local retry_after = math.ceil(shortfall / refill_per_sec)
    if retry_after < 1 then
        retry_after = 1
    end
    return {
        'RATE_LIMITED',
        tostring(retry_after),
        tostring(burst),
        tostring(math.floor(tokens))
    }
end

-- 3. Reserve tokens and monthly allowance
tokens = tokens - cost
redis.call('HSET', tb_key, 'tokens', tokens, 'last_ts', last_ts)
redis.call('EXPIRE', tb_key, 86400)

local new_quota = redis.call('INCRBY', quota_key, cost)
redis.call('EXPIRE', quota_key, seconds_until_next_month + 86400)

return {
    'ALLOWED',
    tostring(math.floor(tokens)),
    tostring(burst),
    tostring(math.max(0, monthly_allowance - new_quota)),
    tostring(monthly_allowance)
}
"""

REFUND_LUA_SCRIPT = """
local tb_key = KEYS[1]
local quota_key = KEYS[2]
local cost = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])

-- Decrement monthly quota
local current_quota = tonumber(redis.call('GET', quota_key) or '0')
if current_quota > 0 then
    redis.call('DECRBY', quota_key, cost)
end

-- Return tokens to bucket
local tb = redis.call('HGET', tb_key, 'tokens')
if tb ~= nil then
    local tokens = tonumber(tb)
    tokens = math.min(burst, tokens + cost)
    redis.call('HSET', tb_key, 'tokens', tokens)
end

return 'OK'
"""


def get_current_utc_month_info():
    """Returns (YYYYMM string, seconds_until_next_month_start, reset_at_timestamp)."""
    now = datetime.now(timezone.utc)
    yyyymm = now.strftime("%Y%m")

    # Next month start
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    seconds_until_next_month = int((next_month - now).total_seconds())
    reset_at = int(next_month.timestamp())
    return yyyymm, max(1, seconds_until_next_month), reset_at


class RateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.r = redis_client

    async def check_and_reserve(
        self,
        org_id: int,
        burst: int,
        refill_per_sec: float,
        monthly_allowance: int,
        cost: int = 1,
    ) -> Dict[str, Any]:
        """Runs the atomic token-bucket and monthly quota reservation in Redis."""
        yyyymm, seconds_until_next_month, next_month_ts = get_current_utc_month_info()
        tb_key = f"ratelimit:{org_id}"
        quota_key = f"quota:{org_id}:{yyyymm}"

        res = await self.r.eval(
            RATE_LIMIT_LUA_SCRIPT,
            2,
            tb_key,
            quota_key,
            cost,
            burst,
            refill_per_sec,
            monthly_allowance,
            seconds_until_next_month,
        )

        status = res[0]
        if status == "ALLOWED":
            return {
                "allowed": True,
                "remaining_tokens": int(res[1]),
                "burst": int(res[2]),
                "remaining_monthly": int(res[3]),
                "monthly_allowance": int(res[4]),
                "reset_at": next_month_ts,
            }
        elif status == "RATE_LIMITED":
            retry_after = int(res[1])
            return {
                "allowed": False,
                "reason": "rate_limited",
                "retry_after_seconds": retry_after,
                "limit": int(res[2]),
                "remaining": int(res[3]),
                "reset_at": int(datetime.now(timezone.utc).timestamp()) + retry_after,
            }
        elif status == "QUOTA_EXHAUSTED":
            return {
                "allowed": False,
                "reason": "quota_exhausted",
                "retry_after_seconds": int(res[1]),
                "limit": int(res[2]),
                "remaining": int(res[3]),
                "reset_at": next_month_ts,
            }
        else:
            raise RuntimeError(f"Unexpected rate limit script response: {res}")

    async def refund(self, org_id: int, burst: int, cost: int = 1):
        """Refunds the reservation if Postgres persistence fails."""
        yyyymm, _, _ = get_current_utc_month_info()
        tb_key = f"ratelimit:{org_id}"
        quota_key = f"quota:{org_id}:{yyyymm}"

        await self.r.eval(REFUND_LUA_SCRIPT, 2, tb_key, quota_key, cost, burst)
