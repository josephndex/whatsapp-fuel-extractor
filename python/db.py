from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, MetaData, Table, Column
from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

try:
    from .env import get_env
except ImportError:
    from env import get_env


def build_db_url() -> str:
    """Build DATABASE_URL from individual params or use DATABASE_URL directly."""
    # Try full URL first
    db_url = get_env('DATABASE_URL')
    if db_url:
        return db_url

    # Otherwise build from individual params
    db_host = get_env('DB_HOST')
    db_name = get_env('DB_NAME')
    db_user = get_env('DB_USER')
    db_password = get_env('DB_PASSWORD')
    db_port = get_env('DB_PORT', '5432')  # default Postgres; use 3306 for MySQL
    db_driver = get_env('DB_DRIVER', 'postgresql+psycopg2')  # or mysql+pymysql

    if db_host and db_name and db_user and db_password:
        # URL-encode password in case it contains special chars
        return f"{db_driver}://{db_user}:{quote_plus(db_password)}@{db_host}:{db_port}/{db_name}"

    raise ValueError(
        'Database not configured. Provide DATABASE_URL or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD in .env'
    )


class Database:
    """Simple DB helper for inserting fuel records.

    Connection options (set in .env):
      Option A: DATABASE_URL (full SQLAlchemy URL)
      Option B: Individual params: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, DB_DRIVER
    """

    def __init__(self, table_name: str = 'fuel_records', engine: Optional[Engine] = None):
        db_url = build_db_url()

        self.engine = engine or create_engine(db_url, pool_pre_ping=True)
        self.meta = MetaData()
        self.table_name = table_name

        # Define table
        self.fuel_records = Table(
            self.table_name,
            self.meta,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('created_at', DateTime, nullable=False, default=datetime.utcnow),
            Column('datetime', String(32), nullable=True),
            Column('department', String(64), nullable=True),
            Column('driver', String(128), nullable=True),
            Column('car', String(32), nullable=False, index=True),
            Column('liters', Float, nullable=True),
            Column('amount', Float, nullable=True),
            Column('type', String(32), nullable=True),
            Column('odometer', Integer, nullable=True),
            Column('sender', String(128), nullable=True),
            Column('raw_message', Text, nullable=True),
        )

        # Create table if not exists
        self.meta.create_all(self.engine, checkfirst=True)

    def insert_fuel_record(self, record: Dict) -> bool:
        try:
            data = {
                'datetime': record.get('datetime'),
                'department': record.get('department'),
                'driver': record.get('driver'),
                'car': record.get('car'),
                'liters': _to_float(record.get('liters')),
                'amount': _to_float(record.get('amount')),
                'type': record.get('type'),
                'odometer': _to_int(record.get('odometer')),
                'sender': record.get('sender'),
                'raw_message': record.get('raw_message'),
            }
            with self.engine.begin() as conn:
                conn.execute(self.fuel_records.insert().values(**data))
            return True
        except SQLAlchemyError as e:
            # Let caller log
            return False

    def update_fuel_record(self, original_datetime: str, original_car: str, new_record: Dict) -> bool:
        """Update an existing record by datetime and car (used for edit approvals)."""
        try:
            data = {
                'department': new_record.get('department'),
                'driver': new_record.get('driver'),
                'car': new_record.get('car'),
                'liters': _to_float(new_record.get('liters')),
                'amount': _to_float(new_record.get('amount')),
                'type': new_record.get('type'),
                'odometer': _to_int(new_record.get('odometer')),
                'sender': new_record.get('sender'),
                'raw_message': new_record.get('raw_message'),
            }
            with self.engine.begin() as conn:
                result = conn.execute(
                    self.fuel_records.update()
                    .where(self.fuel_records.c.datetime == original_datetime)
                    .where(self.fuel_records.c.car == original_car)
                    .values(**data)
                )
                return result.rowcount > 0
        except SQLAlchemyError as e:
            return False


    def get_all_records(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> list:
        """Fetch all fuel records with optional date filtering."""
        try:
            from sqlalchemy import select, text
            
            query = select(self.fuel_records)
            
            # Apply date filters if provided
            conditions = []
            if start_date:
                conditions.append(self.fuel_records.c.created_at >= start_date)
            if end_date:
                conditions.append(self.fuel_records.c.created_at <= end_date)
            
            if conditions:
                for cond in conditions:
                    query = query.where(cond)
            
            query = query.order_by(self.fuel_records.c.created_at.desc())
            
            with self.engine.connect() as conn:
                result = conn.execute(query)
                rows = result.fetchall()
                
                records = []
                for row in rows:
                    # Convert row to dict
                    record = {
                        'id': row.id if hasattr(row, 'id') else row[0],
                        'created_at': row.created_at if hasattr(row, 'created_at') else row[1],
                        'datetime': row.datetime if hasattr(row, 'datetime') else row[2],
                        'department': row.department if hasattr(row, 'department') else row[3],
                        'driver': row.driver if hasattr(row, 'driver') else row[4],
                        'car': row.car if hasattr(row, 'car') else row[5],
                        'liters': row.liters if hasattr(row, 'liters') else row[6],
                        'amount': row.amount if hasattr(row, 'amount') else row[7],
                        'type': row.type if hasattr(row, 'type') else row[8],
                        'odometer': row.odometer if hasattr(row, 'odometer') else row[9],
                        'sender': row.sender if hasattr(row, 'sender') else row[10],
                    }
                    records.append(record)
                return records
        except SQLAlchemyError as e:
            print(f"Database query error: {e}")
            return []

    def get_record_count(self) -> int:
        """Get total number of fuel records."""
        try:
            from sqlalchemy import func, select
            query = select(func.count()).select_from(self.fuel_records)
            with self.engine.connect() as conn:
                result = conn.execute(query)
                return result.scalar() or 0
        except SQLAlchemyError:
            return 0


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _to_int(v):
    try:
        return int(v) if v is not None else None
    except Exception:
        try:
            return int(float(str(v).replace(',', '')))
        except Exception:
            return None
