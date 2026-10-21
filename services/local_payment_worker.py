"""Local-mode renewals while the Flask process is running; no external scheduler needed."""
import threading


def start_worker(app):
    if app.testing or not app.config.get('LOCAL_PAYMENT_WORKER',True):
        return
    lock=app.extensions.setdefault('local_payment_worker_lock',threading.Lock())
    with lock:
        if app.extensions.get('local_payment_worker'):
            return
        stop=threading.Event()
        def run():
            while not stop.is_set():
                try:
                    with app.app_context():
                        from services.billing_routes import return_url, card_secret
                        from services.local_gateway import LocalGateway
                        from services.billing_service import renew_due
                        renew_due(LocalGateway(),card_secret(),return_url)
                except Exception:
                    app.logger.exception('Local payment renewal check failed')
                stop.wait(15)
        thread=threading.Thread(target=run,name='local-payment-renewals',daemon=True)
        app.extensions['local_payment_worker']={'thread':thread,'stop':stop}
        thread.start()
