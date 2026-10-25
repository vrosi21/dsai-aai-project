// The Cloudflare side of the site: the Pages project that owns the public address.
//
// A Pages project normally serves static files. With a file named _worker.js in it
// ("advanced mode"), Cloudflare instead hands every request to this code. All it does is
// fetch the same path from the real server and pass the answer back, so the public
// address works for a Django site that lives somewhere else.
//
// Two settings of the Pages project, both stored as encrypted secrets:
//   ORIGIN_URL     where the real server is, e.g. https://origin.example.com
//   ORIGIN_SECRET  sent on every request; the server ignores anyone who does not have it
//
// Put it online with deploy/setup-cloudflare.sh (see docs/DEPLOYMENT.md).

export default {
  async fetch(request, env) {
    if (!env.ORIGIN_URL || !env.ORIGIN_SECRET) {
      return new Response("This address is not connected to its server yet.", { status: 503 });
    }

    const asked = new URL(request.url);
    const target = new URL(asked.pathname + asked.search, env.ORIGIN_URL);

    // Same method, headers and body as the visitor sent, to the other address.
    const upstream = new Request(target, request);
    upstream.headers.set("X-Origin-Auth", env.ORIGIN_SECRET);

    // Who the visitor is, for the site's rate limits (config/ratelimit.py). Cloudflare's
    // own CF-Connecting-IP does not survive the trip to a server that is itself behind
    // Cloudflare (a Tunnel): every visitor would arrive with the same placeholder. What a
    // visitor sent under this name is never passed on.
    const visitor = request.headers.get("CF-Connecting-IP");
    if (visitor) {
      upstream.headers.set("X-Visitor-IP", visitor);
    } else {
      upstream.headers.delete("X-Visitor-IP");
    }

    try {
      // "manual": a redirect from Django goes back to the browser, which follows it at the
      // public address. Following it here would return the wrong page under the old URL.
      return await fetch(upstream, { redirect: "manual" });
    } catch {
      return new Response("The site is restarting. Try again in a minute.", {
        status: 502,
        headers: { "Retry-After": "30" },
      });
    }
  },
};
