#!/usr/bin/env python3
"""Generate the NON-CURATED TPC-DS query .json files from the raw templates in queries.md.

POLICY (anti-bias, see novas_direcoes_paper.md §7.1): parameters are FIXED DETERMINISTIC values within the
spec-defined ranges (declared below), NOT chosen for opportunity. These are PostgreSQL-dialect drafts
(primary engine) — MySQL versions of these 10 need dsqgen (rollup/||/quoted-idents differ). UNTESTED here
(no TPC-DS DB in this env): verify each runs; a query that fails to run is itself a datapoint (log it).

Declared parameters: YEAR=2000 (q73=1999), GEN='M', MS='M', ES='College', states=common 2-letter codes,
HOUR=20, MONTH=9, DMS(month_seq)=1200, DEPCNT=4, buy_potential='1001-5000'/'0-500', AGG=sum.
"""
import json, os

OUT = "queries/non-curated/tpcds"
os.makedirs(OUT, exist_ok=True)

Q = {}

Q["q5"] = """with ssr as (select s_store_id, sum(sales_price) as sales, sum(profit) as profit, sum(return_amt) as returns, sum(net_loss) as profit_loss
 from (select ss_store_sk as store_sk, ss_sold_date_sk as date_sk, ss_ext_sales_price as sales_price, ss_net_profit as profit, cast(0 as decimal(7,2)) as return_amt, cast(0 as decimal(7,2)) as net_loss from store_sales
   union all
   select sr_store_sk as store_sk, sr_returned_date_sk as date_sk, cast(0 as decimal(7,2)) as sales_price, cast(0 as decimal(7,2)) as profit, sr_return_amt as return_amt, sr_net_loss as net_loss from store_returns) salesreturns, date_dim, store
 where date_sk = d_date_sk and d_date between cast('2000-08-01' as date) and (cast('2000-08-01' as date) + interval '14' day) and store_sk = s_store_sk group by s_store_id),
 csr as (select cp_catalog_page_id, sum(sales_price) as sales, sum(profit) as profit, sum(return_amt) as returns, sum(net_loss) as profit_loss
 from (select cs_catalog_page_sk as page_sk, cs_sold_date_sk as date_sk, cs_ext_sales_price as sales_price, cs_net_profit as profit, cast(0 as decimal(7,2)) as return_amt, cast(0 as decimal(7,2)) as net_loss from catalog_sales
   union all
   select cr_catalog_page_sk as page_sk, cr_returned_date_sk as date_sk, cast(0 as decimal(7,2)) as sales_price, cast(0 as decimal(7,2)) as profit, cr_return_amount as return_amt, cr_net_loss as net_loss from catalog_returns) salesreturns, date_dim, catalog_page
 where date_sk = d_date_sk and d_date between cast('2000-08-01' as date) and (cast('2000-08-01' as date) + interval '14' day) and page_sk = cp_catalog_page_sk group by cp_catalog_page_id),
 wsr as (select web_site_id, sum(sales_price) as sales, sum(profit) as profit, sum(return_amt) as returns, sum(net_loss) as profit_loss
 from (select ws_web_site_sk as wsr_web_site_sk, ws_sold_date_sk as date_sk, ws_ext_sales_price as sales_price, ws_net_profit as profit, cast(0 as decimal(7,2)) as return_amt, cast(0 as decimal(7,2)) as net_loss from web_sales
   union all
   select ws_web_site_sk as wsr_web_site_sk, wr_returned_date_sk as date_sk, cast(0 as decimal(7,2)) as sales_price, cast(0 as decimal(7,2)) as profit, wr_return_amt as return_amt, wr_net_loss as net_loss from web_returns left outer join web_sales on (wr_item_sk = ws_item_sk and wr_order_number = ws_order_number)) salesreturns, date_dim, web_site
 where date_sk = d_date_sk and d_date between cast('2000-08-01' as date) and (cast('2000-08-01' as date) + interval '14' day) and wsr_web_site_sk = web_site_sk group by web_site_id)
 select channel, id, sum(sales) as sales, sum(returns) as returns, sum(profit) as profit
 from (select 'store channel' as channel, 'store' || s_store_id as id, sales, returns, (profit - profit_loss) as profit from ssr
 union all select 'catalog channel' as channel, 'catalog_page' || cp_catalog_page_id as id, sales, returns, (profit - profit_loss) as profit from csr
 union all select 'web channel' as channel, 'web_site' || web_site_id as id, sales, returns, (profit - profit_loss) as profit from wsr) x
 group by rollup (channel, id) order by channel, id limit 100"""

Q["q18"] = """select i_item_id, ca_country, ca_state, ca_county, avg(cast(cs_quantity as decimal(12,2))) agg1, avg(cast(cs_list_price as decimal(12,2))) agg2, avg(cast(cs_coupon_amt as decimal(12,2))) agg3, avg(cast(cs_sales_price as decimal(12,2))) agg4, avg(cast(cs_net_profit as decimal(12,2))) agg5, avg(cast(c_birth_year as decimal(12,2))) agg6, avg(cast(cd1.cd_dep_count as decimal(12,2))) agg7
from catalog_sales, customer_demographics cd1, customer_demographics cd2, customer, customer_address, date_dim, item
where cs_sold_date_sk = d_date_sk and cs_item_sk = i_item_sk and cs_bill_cdemo_sk = cd1.cd_demo_sk and cs_bill_customer_sk = c_customer_sk and cd1.cd_gender = 'M' and cd1.cd_education_status = 'College' and c_current_cdemo_sk = cd2.cd_demo_sk and c_current_addr_sk = ca_address_sk and c_birth_month in (1,2,3,4,5,6) and d_year = 2000 and ca_state in ('TN','CA','TX','NY','FL','OH','GA')
group by rollup (i_item_id, ca_country, ca_state, ca_county) order by ca_country, ca_state, ca_county, i_item_id limit 100"""

Q["q25"] = """select i_item_id, i_item_desc, s_store_id, s_store_name, sum(ss_net_profit) as store_sales_profit, sum(sr_net_loss) as store_returns_loss, sum(cs_net_profit) as catalog_sales_profit
from store_sales, store_returns, catalog_sales, date_dim d1, date_dim d2, date_dim d3, store, item
where d1.d_moy = 4 and d1.d_year = 2000 and d1.d_date_sk = ss_sold_date_sk and i_item_sk = ss_item_sk and s_store_sk = ss_store_sk and ss_customer_sk = sr_customer_sk and ss_item_sk = sr_item_sk and ss_ticket_number = sr_ticket_number and sr_returned_date_sk = d2.d_date_sk and d2.d_moy between 4 and 10 and d2.d_year = 2000 and sr_customer_sk = cs_bill_customer_sk and sr_item_sk = cs_item_sk and cs_sold_date_sk = d3.d_date_sk and d3.d_moy between 4 and 10 and d3.d_year = 2000
group by i_item_id, i_item_desc, s_store_id, s_store_name order by i_item_id, i_item_desc, s_store_id, s_store_name limit 100"""

Q["q27"] = """select i_item_id, s_state, grouping(s_state) g_state, avg(ss_quantity) agg1, avg(ss_list_price) agg2, avg(ss_coupon_amt) agg3, avg(ss_sales_price) agg4
from store_sales, customer_demographics, date_dim, store, item
where ss_sold_date_sk = d_date_sk and ss_item_sk = i_item_sk and ss_store_sk = s_store_sk and ss_cdemo_sk = cd_demo_sk and cd_gender = 'M' and cd_marital_status = 'M' and cd_education_status = 'College' and d_year = 2000 and s_state in ('TN','CA','TX','NY','FL','OH')
group by rollup (i_item_id, s_state) order by i_item_id, s_state limit 100"""

Q["q40"] = """select w_state, i_item_id, sum(case when (cast(d_date as date) < cast('2000-04-01' as date)) then cs_sales_price - coalesce(cr_refunded_cash,0) else 0 end) as sales_before, sum(case when (cast(d_date as date) >= cast('2000-04-01' as date)) then cs_sales_price - coalesce(cr_refunded_cash,0) else 0 end) as sales_after
from catalog_sales left outer join catalog_returns on (cs_order_number = cr_order_number and cs_item_sk = cr_item_sk), warehouse, item, date_dim
where i_current_price between 0.99 and 1.49 and i_item_sk = cs_item_sk and cs_warehouse_sk = w_warehouse_sk and cs_sold_date_sk = d_date_sk and d_date between (cast('2000-04-01' as date) - interval '30' day) and (cast('2000-04-01' as date) + interval '30' day)
group by w_state, i_item_id order by w_state, i_item_id limit 100"""

Q["q50"] = """select s_store_name, s_company_id, s_street_number, s_street_name, s_street_type, s_suite_number, s_city, s_county, s_state, s_zip, sum(case when (sr_returned_date_sk - ss_sold_date_sk <= 30) then 1 else 0 end) as "30 days", sum(case when (sr_returned_date_sk - ss_sold_date_sk > 30) and (sr_returned_date_sk - ss_sold_date_sk <= 60) then 1 else 0 end) as "31-60 days", sum(case when (sr_returned_date_sk - ss_sold_date_sk > 60) and (sr_returned_date_sk - ss_sold_date_sk <= 90) then 1 else 0 end) as "61-90 days", sum(case when (sr_returned_date_sk - ss_sold_date_sk > 90) and (sr_returned_date_sk - ss_sold_date_sk <= 120) then 1 else 0 end) as "91-120 days", sum(case when (sr_returned_date_sk - ss_sold_date_sk > 120) then 1 else 0 end) as ">120 days"
from store_sales, store_returns, store, date_dim d1, date_dim d2
where d2.d_year = 2000 and d2.d_moy = 9 and ss_ticket_number = sr_ticket_number and ss_item_sk = sr_item_sk and ss_sold_date_sk = d1.d_date_sk and sr_returned_date_sk = d2.d_date_sk and ss_customer_sk = sr_customer_sk and ss_store_sk = s_store_sk
group by s_store_name, s_company_id, s_street_number, s_street_name, s_street_type, s_suite_number, s_city, s_county, s_state, s_zip
order by s_store_name, s_company_id, s_street_number, s_street_name, s_street_type, s_suite_number, s_city, s_county, s_state, s_zip limit 100"""

Q["q63"] = """select * from (select i_manager_id, sum(ss_sales_price) sum_sales, avg(sum(ss_sales_price)) over (partition by i_manager_id) avg_monthly_sales
 from item, store_sales, date_dim, store
 where ss_item_sk = i_item_sk and ss_sold_date_sk = d_date_sk and ss_store_sk = s_store_sk and d_month_seq in (1200,1201,1202,1203,1204,1205,1206,1207,1208,1209,1210,1211) and ((i_category in ('Books','Children','Electronics') and i_class in ('personal','portable','reference','self-help') and i_brand in ('scholaramalgamalg #14','scholaramalgamalg #7','exportiunivamalg #9','scholaramalgamalg #9')) or (i_category in ('Women','Music','Men') and i_class in ('accessories','classical','fragrances','pants') and i_brand in ('amalgimporto #1','edu packscholar #1','exportiimporto #1','importoamalg #1')))
 group by i_manager_id, d_moy) tmp1
where case when avg_monthly_sales > 0 then abs(sum_sales - avg_monthly_sales) / avg_monthly_sales else null end > 0.1
order by i_manager_id, avg_monthly_sales, sum_sales limit 100"""

Q["q73"] = """select c_last_name, c_first_name, c_salutation, c_preferred_cust_flag, ss_ticket_number, cnt from
 (select ss_ticket_number, ss_customer_sk, count(*) cnt
  from store_sales, date_dim, store, household_demographics
  where store_sales.ss_sold_date_sk = date_dim.d_date_sk and store_sales.ss_store_sk = store.s_store_sk and store_sales.ss_hdemo_sk = household_demographics.hd_demo_sk and date_dim.d_dom between 1 and 2 and (household_demographics.hd_buy_potential = '1001-5000' or household_demographics.hd_buy_potential = '0-500') and household_demographics.hd_vehicle_count > 0 and case when household_demographics.hd_vehicle_count > 0 then household_demographics.hd_dep_count / household_demographics.hd_vehicle_count else null end > 1 and date_dim.d_year in (1999,2000,2001) and store.s_county in ('Williamson County','Walker County','Ziebach County','Daviess County')
  group by ss_ticket_number, ss_customer_sk) dj, customer
 where ss_customer_sk = c_customer_sk and cnt between 1 and 5 order by cnt desc, c_last_name asc"""

Q["q85"] = """select substr(r_reason_desc,1,20), avg(ws_quantity), avg(wr_refunded_cash), avg(wr_fee)
from web_sales, web_returns, web_page, customer_demographics cd1, customer_demographics cd2, customer_address, date_dim, reason
where ws_web_page_sk = wp_web_page_sk and ws_item_sk = wr_item_sk and ws_order_number = wr_order_number and ws_sold_date_sk = d_date_sk and d_year = 2000 and cd1.cd_demo_sk = wr_refunded_cdemo_sk and cd2.cd_demo_sk = wr_returning_cdemo_sk and ca_address_sk = wr_refunded_addr_sk and r_reason_sk = wr_reason_sk and ((cd1.cd_marital_status = 'M' and cd1.cd_marital_status = cd2.cd_marital_status and cd1.cd_education_status = 'College' and cd1.cd_education_status = cd2.cd_education_status and ws_sales_price between 100.00 and 150.00) or (cd1.cd_marital_status = 'S' and cd1.cd_marital_status = cd2.cd_marital_status and cd1.cd_education_status = 'Primary' and cd1.cd_education_status = cd2.cd_education_status and ws_sales_price between 50.00 and 100.00) or (cd1.cd_marital_status = 'D' and cd1.cd_marital_status = cd2.cd_marital_status and cd1.cd_education_status = 'Secondary' and cd1.cd_education_status = cd2.cd_education_status and ws_sales_price between 150.00 and 200.00)) and ((ca_country = 'United States' and ca_state in ('TN','CA','TX') and ws_net_profit between 100 and 200) or (ca_country = 'United States' and ca_state in ('NY','FL','OH') and ws_net_profit between 150 and 300) or (ca_country = 'United States' and ca_state in ('GA','IL','PA') and ws_net_profit between 50 and 250))
group by r_reason_desc order by substr(r_reason_desc,1,20), avg(ws_quantity), avg(wr_refunded_cash), avg(wr_fee) limit 100"""

Q["q96"] = """select count(*) from store_sales, household_demographics, time_dim, store
where ss_sold_time_sk = time_dim.t_time_sk and ss_hdemo_sk = household_demographics.hd_demo_sk and ss_store_sk = s_store_sk and time_dim.t_hour = 20 and time_dim.t_minute >= 30 and household_demographics.hd_dep_count = 4 and store.s_store_name = 'ese'
order by count(*) limit 100"""

for name, sql in Q.items():
    sql = " ".join(sql.split())  # collapse whitespace to one line, like the existing files
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        json.dump({"sql": sql}, f)
print(f"Wrote {len(Q)} files to {OUT}/: {', '.join(sorted(Q))}")
