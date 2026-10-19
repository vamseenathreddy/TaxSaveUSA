# TaxSaveUSA: path to $100/day (6-month plan)

Goal: about $3,000/month of channel income by April 2027, the end of US tax season.

## Honest maths
YouTube Shorts pay roughly $0.02 to $0.10 per 1,000 views, so Shorts alone would need
1M+ US views a day. The plan therefore treats Shorts as the front door and earns mainly from:
1. Affiliate commissions (tax filing software, savings accounts, budgeting tools). Peak: Jan to Apr.
2. Long-form ads. Finance pays $8 to $15+ per 1,000 views, about 100x Shorts. Needs YouTube Partner Program (YPP).
3. A free tax checklist that builds an email list, then promoted offers.
4. Website ads (AdSense) once the site has steady traffic.

Realistic outcome: $100/day is a stretch goal. A good result in 6 months is $20 to $60/day,
with the peak in tax season. This plan maximises the chance of the top end.

## Phases
| Phase | Dates | Focus | Target |
|---|---|---|---|
| 1 Foundation | Oct to Nov 2026 | Fix token, post daily, finance-only niche, apply for affiliate programs, set up email list | 300 subs |
| 2 Traction | Dec 2026 | Study analytics, double down on best formats, long video weekly | 1,000 subs |
| 3 Tax season | Jan to Apr 2027 | Heavy tax content, affiliate links in every description, newsletter pushes | YPP + affiliates live, $2 to $5k/month |
| 4 Review | Apr 2027 | Keep what earned, cut the rest | $100/day average if tax season works |

## YouTube Partner Program
- Early tier (500 subs + 3 public uploads in 90 days): fan funding, no ads.
- Full ads: 1,000 subs plus either 4,000 public watch hours in 12 months or 10M Shorts views in 90 days.
- Long videos are the quickest way to watch hours. Keep the daily long video.

## Content rules that raise earnings
- Money, tax, jobs, prices, housing, retirement, benefits only. Off-niche stories are skipped
  (`FINANCE_ONLY` in `scripts/trending.py`).
- Every Short and long video links to one resource (checklist or affiliate) with the disclosure line.
- Facts from primary sources (IRS, SSA, CFPB) for tax content. Trending stories credited to their publisher.
- Do not use clickbait or false urgency: it hurts watch time and risks demonetisation.

## Automated now
- Posting mix, pace review and playlists (see `config/rollout.json`, `scripts/analytics_pace.py`).
- Links and disclosure added from `config/monetization.json` to every video description and the website.

## Needs the owner (accounts only you can create)
1. Refresh the YouTube token (blocks all posting).
2. Turn on GitHub Pages (`docs` folder).
3. Sign up for affiliate programs (tax software, savings, budgeting) via marketplaces such as Impact or CJ,
   then paste the links into `config/monetization.json`.
4. Create a free email list (Buttondown or MailerLite) and a free "2027 tax checklist" PDF; paste the signup link in `config/monetization.json`.
5. Apply for AdSense on the website once it has about 30 posts.

## Weekly check (10 minutes)
Views per Short, subscribers, watch hours, clicks on links, revenue. `state/analytics.json` and `state/pace.json` hold the numbers.
