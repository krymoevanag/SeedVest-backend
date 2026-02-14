from django.core.management.base import BaseCommand
from payments.services.stk_push import stk_push
from payments.models import MpesaTransaction
import json

class Command(BaseCommand):
    help = 'Test M-Pesa STK Push via terminal'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('--- M-Pesa STK Push Tester ---'))
        
        try:
            phone = input("Enter Phone Number (e.g., 254712345678): ").strip()
            if not phone:
                self.stdout.write(self.style.ERROR('Phone number is required'))
                return

            amount_raw = input("Enter Amount: ").strip()
            if not amount_raw:
                self.stdout.write(self.style.ERROR('Amount is required'))
                return
            
            try:
                amount = int(amount_raw)
            except ValueError:
                self.stdout.write(self.style.ERROR('Amount must be an integer'))
                return

            self.stdout.write(f"Initiating STK Push for {phone} with amount {amount}...")
            
            response = stk_push(phone, amount)
            
            self.stdout.write(self.style.SUCCESS('Response from M-Pesa:'))
            self.stdout.write(json.dumps(response, indent=4))
            
            if "CheckoutRequestID" in response:
                # Save transaction to database so callback can find it
                MpesaTransaction.objects.create(
                    phone_number=phone,
                    amount=amount,
                    checkout_request_id=response["CheckoutRequestID"],
                    merchant_request_id=response.get("MerchantRequestID", ""),
                    status="PENDING"
                )
                self.stdout.write(self.style.SUCCESS(f"STK Push Sent & Saved! CheckoutRequestID: {response['CheckoutRequestID']}"))
                self.stdout.write("Wait for the prompt on your phone and check the logs/database for callback updates.")
            else:
                self.stdout.write(self.style.ERROR("STK Push Failed! Check the response above."))

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nOperation cancelled by user.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'An error occurred: {str(e)}'))
