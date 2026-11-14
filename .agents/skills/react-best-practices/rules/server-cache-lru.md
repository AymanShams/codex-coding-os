---
title: Cross-Request LRU Caching
impact: HIGH
impactDescription: caches across requests
tags: server, cache, lru, cross-request
---

## Cross-Request LRU Caching

`React.cache()` only works within one request. For data shared across sequential requests (user clicks button A then button B), consider an LRU cache when repeated reads justify reuse.

**Implementation:**

```typescript
import { LRUCache } from 'lru-cache'

const cache = new LRUCache<string, any>({
  max: 1000,
  ttl: 5 * 60 * 1000  // 5 minutes
})

export async function getUser(id: string) {
  const cached = cache.get(id)
  if (cached) return cached

  const user = await db.user.findUnique({ where: { id } })
  cache.set(id, user)
  return user
}

// Request 1: DB query, result cached
// Request 2: cache hit, no DB query
```

Use when sequential user actions hit multiple endpoints needing the same data within seconds.

Choose the size limit and acceptable freshness for the actual workload rather than copying the example values. Include user/account context in keys when results differ by that context. Invalidate or refresh affected entries after updates or relevant account changes.

**With Vercel's [Fluid Compute](https://vercel.com/docs/fluid-compute):** Requests handled by the same function instance can reuse its in-memory cache. Other instances have separate caches.

**In other serverless runtimes:** An instance may be reused, but reuse and lifetime are not guaranteed. This cache is instance-local and non-durable. On a miss or instance replacement, read the authoritative source and preserve existing error handling. Keep fallback reads within the source's capacity.

Reference: [https://github.com/isaacs/node-lru-cache](https://github.com/isaacs/node-lru-cache)
