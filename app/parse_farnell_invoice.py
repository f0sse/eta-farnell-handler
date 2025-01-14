import tabula
import os
import re
from collections import defaultdict

from app.models import ToMemberInvoice, FarnellItem, Person

def get_item_count(line1):
    has_first_number = False
    next_number_is_count = False
    # item count is the first possible number that can be converted to an int after the unit
    for item in line1:
        # sometimes spaces are added
        for cell in str(item).split():
            if cell:
                try:
                    val = int(cell)
                    if next_number_is_count:
                        return val
                    if not has_first_number:
                        has_first_number = True
                except ValueError:
                    if has_first_number:
                        next_number_is_count = True
    raise Exception("NO ITEM COUNT FOUND IN LINE", line1)
    
def is_end_of_order_table(table):
    end_of_table_ids = ["ER REFERENS  ETA INKÖP", "Utgående", "MYCKET VIKTIGT"]
    return table[0][0] in end_of_table_ids
    
def parse_order_table(table, invoice_no):
    items = []
    # remove header
    order_date_line = table.pop(0)
    table.pop(0)
    table.pop(0)
    # some pages have additional header
    if table[0][0] == "Ingående":
        table.pop(0)
    
    order_date = order_date_line[0].split()[2]
    # check for new date convention: ETAnnnn-yymmdd, ETAnnnnyymmdd
    if order_date.startswith("ETA"):
        _d = re.match(r"ETA....-?(?P<y>[0-9]{2})(?P<m>[0-9]{2})(?P<d>[0-9]{2})", order_date).groupdict()
        order_date = f"20{_d['y']}-{_d['m']}-{_d['d']}"
    
    while table:
        if is_end_of_order_table(table):
            break
    
        line1 = table.pop(0)
        # shipping is a single line item without VAT
        if "FRAKT" in line1:
            cost = float(line1[-1])
            item_desc = "FRAKT"
            name = "ETA"
            art_no = "FRAKT"
            item_count = 1
        # alla vouchers går till eta, det kassör får göra handpåläggning om det ska till medlem
        elif line1[0] == "VOUCHER":
            continue
        # eta betalar all re reeling (eller handpåläggning av kassör för att lösa det)
        elif line1[0] == "RE REEL":
            continue
        else:
            line2 = table.pop(0)
            # some invoices have a note above the line comment, remove that if found
            if "Despatch Note No " in table[0][0]:
                table.pop(0)
            # re reeled items have an additional line before the line comment, ignore it
            if "RE REEL" in table[0][0]:
                table.pop(0) 
            # some invoices have only 2 lines (this happens when no line comment is added)
            # make sure to only consume 2 lines in that case
            if not re.match(r"\d+ \d+", table[0][0]) and not is_end_of_order_table(table):
                name = table.pop(0)[0]
                name = name.upper()
                # farnell adds some shit in some cases that contains ship date and a date lets remove
                name = name.split(" / SHIP DATE:")[0]
            else:
                name = "UNKNOWN"

            art_no = line1[0].split()[1]
            cost = float(line1[-1])
            vat = float(line1[-2])
            cost = cost * (1+vat/100)
            item_desc = line2[0]
            item_count = get_item_count(line1)
        
        
        person, created = Person.objects.all().get_or_create(name=name)
        if created:
            print("Person", name, "was not present in the database. They have been added but phone number, email etc is missing. Please add that info")
        items.append(
            FarnellItem(
                item_count=item_count, 
                item_no=art_no, 
                item_desc=item_desc, 
                person=person, 
                cost=cost, 
                order_placed_at=order_date, 
                invoice_number=invoice_no
            )
        )
        
    return items
    
def invoice_to_items(file_name):
    # read PDF file
    tables = tabula.read_pdf(file_name, pages="all", pandas_options={'header': None})
    items = []
    invoice_no = tables[0][4][0]
    
    for tab in tables:
        table = tab.values.tolist()
        # check if it is an ordertable, other tables are contact stuff
        if "Ert Ordernummer" in table[0][0]:
            items += parse_order_table(table, invoice_no)
    return items
    
def parse_and_save_multiple_invoices(paths):
    items = []
    for path in paths:
        print("Parsing", path)
        items += invoice_to_items(path)
        
    group_by_name = defaultdict(list)
    for item in items:
        group_by_name[item.person_id].append(item)

    for _, group_items in group_by_name.items():
        invoice = ToMemberInvoice()
        invoice.save()
        for item in group_items:
            item.to_member_invoice = invoice
    FarnellItem.objects.bulk_create(items)
    print("Parsed", len(paths), "invoices. Created", len(items), "items and added", len(group_by_name), "invoices")
