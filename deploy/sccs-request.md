# Email to SCCS, to move the API onto oscarpdalton.com

The demo works today with its backend on a free Hugging Face Space. Moving it
here removes a ~40 s cold start after the Space idles, and a cross-origin hop.
Nothing breaks if the answer is no, so this is a request and not a dependency.

Send to whoever maintains the Swarm — `dcrepublic@sccs.swarthmore.edu` set up
the current `oscar-site` deploy (the `Dockerfile`, `compose.yml` and GHCR
workflow commits are theirs), so they are the right first ask.

Attach or link `deploy/stack.yml` from this repo.

---

**Subject:** Adding a second container to the oscarpdalton.com stack

Hi,

You set up the Docker/Traefik deploy for oscarpdalton.com back in July — thank
you, it has been running without my touching it since.

I've built a demo of a text front end for speech synthesis that I'd like to put
on the site, at `oscarpdalton.com/frontend`. The page itself is just part of the
existing Vite build, so it needs nothing new from you. But two of its three
stages are transformer models, and those need a small Python service rather than
static files.

Could you deploy one more container into the stack? It's a single command on a
manager node, and I've written the stack file so it needs no DNS change and no
new certificate — it's a path prefix on the host that's already routed:

```
docker stack deploy -c stack.yml frontend-api
```

The stack file is attached. The specifics, so you can judge it:

- **Image:** `ghcr.io/daltonoscar0/frontend-demo-api:latest`, built by GitHub
  Actions from https://github.com/daltonoscar0/frontend-demo (public, MIT).
- **Resources:** capped at 1.5 GB memory and 2 CPUs, one replica. Measured peak
  is 778 MB with both models resident; the cap is headroom, not the requirement.
- **Routing:** `PathPrefix(/api/frontend)` on the existing
  `oscarpdalton.com` router, stripped before forwarding. It does not touch the
  `oscar_site` router or service.
- **Network:** joins the existing external `traefik` network. No ports published
  to the host.
- **Traffic:** rate limited to 30 requests a minute per IP in the application,
  with a 500-character cap on input. It's a portfolio demo, so I expect very
  little, but I'd rather the limit exist before it's needed.
- **Updates:** labelled `shepherd.frontend-api.enable=true`, so the shepherd
  container already in the stack rolls it forward from GHCR the same way it does
  `oscar-site`. You wouldn't need to touch it again.
- **Network access at runtime:** none. The model weights and all data are baked
  into the image and `HF_HUB_OFFLINE=1` is set, so it makes no outbound requests
  while serving.

If a second service in the stack is more than you want to take on, I'm happy
with any of: a lower memory cap, a different path, or leaving it where it is on
an external host — the demo works either way and I don't want to create work.

Happy to walk through the stack file or the image build if it's easier.

Thanks,
Oscar Dalton
odalton1@swarthmore.edu
