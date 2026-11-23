from odoo import Command, _, api, fields, models

from . import bank_parser


class AccountBankStatementLine(models.Model):
    """Bank transactions of the rental company, assigned to category and apartment(s).

    Booking logic (no reconciliation widget needed, works in Odoo Community):

    * category + apartment(s) known (or a general category such as bank fees):
      the counterpart line of the journal entry is booked to the account of the
      category with the apartments' cost centres as analytic distribution
      (several apartments = equal split);
    * otherwise the counterpart stays on the suspense account of the bank journal
      ("zu klären"). Setting category/apartments later books the transaction.
    """

    _inherit = "account.bank.statement.line"

    apartment_category_id = fields.Many2one(
        "apartment.booking.category", "Kategorie", index="btree_not_null", ondelete="restrict"
    )
    apartment_ids = fields.Many2many(
        "apartment.apartment", "apartment_bank_line_rel", "line_id", "apartment_id", string="Wohnungen",
        help="Mehrere Wohnungen: der Betrag wird gleichmäßig aufgeteilt",
    )
    apartment_payer = fields.Char("Zahlungspflichtige*r")
    apartment_payee = fields.Char("Zahlungsempfänger*in")
    apartment_value_date = fields.Date("Wertstellung")
    apartment_assignment = fields.Char("Zuordnung (Datei)", help="Text aus Spalte 'Zuordnung' der Importdatei")
    apartment_assignment_info = fields.Char("Zuordnung begründet durch")
    apartment_note = fields.Char("Kommentar")
    apartment_import_key = fields.Char("Import-Schlüssel", copy=False, index="btree_not_null", readonly=True)
    apartment_purpose_key = fields.Char(
        "Zweck-Schlüssel", compute="_compute_apartment_purpose_key", store=True,
        help="Verwendungszweck ohne Datum/Jahr/Beträge – für die Zuordnung neuer Umsätze",
    )

    @api.depends("payment_ref")
    def _compute_apartment_purpose_key(self):
        for rec in self:
            rec.apartment_purpose_key = bank_parser.purpose_key(rec.payment_ref)

    # ------------------------------------------------------------ booking
    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.filtered("apartment_category_id")._apartment_book()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {"apartment_category_id", "apartment_ids"} & vals.keys() and not self.env.context.get(
            "apartment_skip_booking"
        ):
            self._apartment_book()
        return res

    def _apartment_counterpart_vals(self):
        """Counterpart line values for category/apartments, or ``(None, reason)``."""
        self.ensure_one()
        category, apartments = self.apartment_category_id, self.apartment_ids
        if not category:
            return None, _("keine Kategorie")
        if not category.is_general and not apartments:
            return None, _("keine Wohnung")
        distribution = {}
        if not category.is_general:
            missing = apartments.filtered(lambda a: not a.analytic_account_id)
            if missing:
                return None, _("Kostenstelle fehlt bei %s", ", ".join(missing.mapped("name")))
            for apt, pct in zip(apartments, bank_parser.split_percent(len(apartments))):
                distribution[str(apt.analytic_account_id.id)] = pct

        account = category.account_id
        parts = None
        if category.code == "loan":
            loan_accounts = apartments.loan_account_id
            if len(loan_accounts) > 1:
                return None, _("Wohnungen haben verschiedene Darlehenskonten")
            account = loan_accounts or account
            split = bank_parser.loan_parts(self.payment_ref, self.amount)
            interest = self.env["apartment.booking.category"]._by_code(self.company_id).get("interest")
            if split and interest and interest.account_id and account:
                sign = -1 if self.amount < 0 else 1
                parts = [(account, sign * split[0]), (interest.account_id, sign * split[1])]
        if not account:
            return None, _("kein Konto für Kategorie %s", category.name)
        parts = parts or [(account, self.amount)]

        currency = self.journal_id.currency_id or self.company_id.currency_id
        return [
            {
                "name": self.payment_ref or "/",
                "account_id": acc.id,
                "partner_id": self.partner_id.id,
                "currency_id": currency.id,
                "amount_currency": -amount,
                "balance": -amount,
                "analytic_distribution": distribution or False,
            }
            for acc, amount in parts
        ], ""

    def _apartment_set_counterpart(self, vals_list):
        """Replace all non-bank lines of the journal entry by ``vals_list``."""
        self.ensure_one()
        _liquidity, suspense, other = self._seek_for_lines()
        move = self.move_id
        posted = move.state == "posted"
        if posted:
            move.button_draft()
        move.with_context(skip_readonly_check=True).write({
            "line_ids": [Command.delete(line.id) for line in suspense | other]
            + [Command.create(vals) for vals in vals_list],
        })
        if posted:
            move.action_post()

    def _apartment_book(self):
        for line in self:
            vals_list, _reason = line._apartment_counterpart_vals()
            if vals_list is None:
                _liquidity, _suspense, other = line._seek_for_lines()
                if other:  # was booked before: back to the suspense account
                    suspense_vals = line._prepare_move_line_default_vals()[1]
                    suspense_vals.pop("move_id", None)
                    line._apartment_set_counterpart([suspense_vals])
                continue
            line._apartment_set_counterpart(vals_list)

    # ------------------------------------------------------------ classification
    @api.model
    def _apartment_context(self, journal):
        apartments = self.env["apartment.apartment"].search([("company_id", "=", journal.company_id.id)])
        return {
            "journal": journal,
            "apartments": [
                {"id": a.id, "name": a.name, "unit_number": a.unit_number or "", "city": a.city or ""}
                for a in apartments
            ],
            "categories": self.env["apartment.booking.category"]._by_code(journal.company_id),
            "rules": self.env["apartment.bank.rule"].search([("company_id", "=", journal.company_id.id)]),
        }

    @api.model
    def _apartment_classify(self, row, ctx, exclude=None):
        """Return ``(category, apartments, info)`` for a parsed bank row.

        Order: 1. column 'Zuordnung' of the file, 2. user rules, 3. earlier bookings with
        the same IBAN and a similar purpose. Every step only fills what is still missing.
        """
        Category = self.env["apartment.booking.category"]
        Apartment = self.env["apartment.apartment"]
        info = []
        category, apartments = Category, Apartment
        code = bank_parser.category_from_assignment(row.get("assignment"))
        if code and ctx["categories"].get(code):
            category = ctx["categories"][code]
            info.append(_("Datei: %s", row["assignment"]))
            if not category.is_general:
                ids, why = bank_parser.resolve_apartments(
                    row["assignment"], row["purpose"], ctx["apartments"], category.allow_split
                )
                apartments = Apartment.browse(ids)
                if why and (not ids or len(ids) > 1):
                    info.append(why)

        def missing():
            return not category or (not category.is_general and not apartments)

        if missing():
            rule = ctx["rules"]._find_match(row)
            if rule:
                category = category or rule.category_id
                if not apartments and not category.is_general:
                    apartments = rule.apartment_ids
                info.append(_("Regel: %s", rule.name))
        if missing():
            prev = self._apartment_find_previous(row, ctx["journal"], exclude)
            if prev:
                category = category or prev.apartment_category_id
                if not apartments and prev.apartment_ids and category == prev.apartment_category_id:
                    apartments = prev.apartment_ids
                    if len(apartments) == 1:
                        # Zur Mühle I/II: the unit number in the new text wins over the old booking
                        by_unit = bank_parser.apartment_by_unit(row["purpose"], ctx["apartments"], apartments.name)
                        apartments = Apartment.browse(by_unit) if by_unit else apartments
                info.append(_("wie Buchung vom %s", prev.date.strftime("%d.%m.%Y")))
        if category.is_general:
            apartments = Apartment
        return category, apartments, "; ".join(info)

    @api.model
    def _apartment_find_previous(self, row, journal, exclude=None):
        """Most similar earlier categorised transaction (same IBAN or counterparty, same sign)."""
        key = bank_parser.purpose_key(row.get("purpose"))
        if not key:
            return self.browse()
        domain = [
            ("journal_id", "=", journal.id),
            ("apartment_category_id", "!=", False),
            ("amount", ">=" if row["amount"] >= 0 else "<", 0),
        ]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        if row.get("iban"):
            domain.append(("account_number", "=", row["iban"]))
        else:
            counterparty = row["payee"] if row["amount"] < 0 else row["payer"]
            domain += [("account_number", "in", [False, ""]), ("partner_name", "=", counterparty)]
        candidates = self.search(domain, order="date desc, id desc", limit=300)
        if not candidates:
            return self.browse()
        scored = [
            (1.0 if c.apartment_purpose_key == key else bank_parser.similarity(key, c.apartment_purpose_key), c)
            for c in candidates
        ]
        best = max(score for score, _c in scored)
        if best < 0.75:
            return self.browse()
        top = self.browse([c.id for score, c in scored if score >= best - 0.02])
        # same text behind several categories/apartments (e.g. two loans 'Darlehensrate'):
        # prefer the same amount, then booked transactions, then the latest
        same_amount = top.filtered(lambda c: abs(c.amount - row["amount"]) < 0.005)
        top = same_amount or top
        return (top.filtered("is_reconciled") or top)[:1]

    def _apartment_row(self):
        """Bank row dict of an existing transaction (for rules / history)."""
        self.ensure_one()
        return {
            "assignment": self.apartment_assignment or "",
            "purpose": self.payment_ref or "",
            "payer": self.apartment_payer or "",
            "payee": self.apartment_payee or "",
            "iban": self.account_number or "",
            "amount": self.amount,
        }

    # ------------------------------------------------------------ import
    @api.model
    def apartment_import_bank_file(self, journal, content, filename="", update_existing=True):
        """Import a DKB CSV export or a yearly 'umsaetze_YYYY.xlsx' into ``journal``.

        Transactions already imported (same import key) are skipped. With
        ``update_existing`` a transaction that is still on the suspense account gets the
        classification of this file (e.g. raw CSV first, curated xlsx later); booked
        transactions are never changed.
        """
        rows = bank_parser.parse_bank_file(content, filename)
        ctx = self._apartment_context(journal)
        existing = {
            line.apartment_import_key: line
            for line in self.search([
                ("journal_id", "=", journal.id),
                ("apartment_import_key", "in", [r["import_key"] for r in rows]),
            ])
        }
        created, updated, skipped = self.browse(), self.browse(), 0
        # oldest first, so later rows of the same file can learn from earlier ones
        for row in sorted(rows, key=lambda r: (r["date"], r["line_no"])):
            line = existing.get(row["import_key"])
            if line:
                if update_existing and not line.is_reconciled:
                    category, apartments, info = self._apartment_classify(row, ctx, exclude=line)
                    if category and (category.is_general or apartments):
                        line.write({
                            "apartment_category_id": category.id,
                            "apartment_ids": [Command.set(apartments.ids)],
                            "apartment_assignment_info": info,
                            "apartment_assignment": row["assignment"] or line.apartment_assignment,
                        })
                        updated |= line
                        continue
                skipped += 1
                continue
            category, apartments, info = self._apartment_classify(row, ctx)
            counterparty = row["payee"] if row["amount"] < 0 else row["payer"]
            created |= self.create([{
                "journal_id": journal.id,
                "date": row["date"],
                "amount": row["amount"],
                "payment_ref": row["purpose"] or "/",
                "partner_name": counterparty,
                "account_number": row["iban"] or False,
                "transaction_type": row["booking_type"],
                "apartment_payer": row["payer"],
                "apartment_payee": row["payee"],
                "apartment_value_date": row["value_date"],
                "apartment_assignment": row["assignment"],
                "apartment_note": row["comment"] or False,
                "apartment_import_key": row["import_key"],
                "transaction_details": {
                    "creditor_ref": row["creditor_ref"],
                    "mandate_ref": row["mandate_ref"],
                    "customer_ref": row["customer_ref"],
                    "import_file": filename,
                },
                "apartment_category_id": category.id,
                "apartment_ids": [Command.set(apartments.ids)],
                "apartment_assignment_info": info,
            }])
        touched = created | updated
        return {
            "rows": len(rows),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "to_check": touched.filtered(lambda line: not line.is_reconciled),
        }

    def action_apartment_apply_rules(self):
        """Re-run rules and history on the selected transactions that are not booked yet."""
        open_lines = self.filtered(lambda line: not line.is_reconciled)
        for journal, lines in open_lines.grouped("journal_id").items():
            ctx = self._apartment_context(journal)
            for line in lines:
                category, apartments, info = line._apartment_classify(line._apartment_row(), ctx, exclude=line)
                vals = {"apartment_assignment_info": info}
                if category and not line.apartment_category_id:
                    vals["apartment_category_id"] = category.id
                if apartments and not line.apartment_ids:
                    vals["apartment_ids"] = [Command.set(apartments.ids)]
                line.write(vals)
        return True

    def action_apartment_rebook(self):
        """Book again with the current category/apartment settings (e.g. after setup changes)."""
        self._apartment_book()
        return True

    def action_apartment_open_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }
